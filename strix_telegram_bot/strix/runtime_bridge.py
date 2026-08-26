"""StrixRuntimeBridge — thin projection of the official Strix 1.5 TUI for Telegram.

Delegates lifecycle to GoTuiRuntime WITHOUT starting the Go sidecar:
  - GoTuiRuntime creates coordinator, live_view, controller, report_state
  - bridge reuses init_run_state() + start_scan() for setup
  - Agent data via coordinator.graph_snapshot()
  - Events via live_view.events (populated by capture_event)
  - AWAITING_USER via coordinator.wait_kind_of(agent_id) == "user"
  - Cleanup via GoTuiRuntime.quit()

No parallel event queue. No duplicate state. No synthetic lifecycle events.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from strix_telegram_bot.config import settings

logger = logging.getLogger(__name__)

_FINAL_COMPLETED = "completed"
_FINAL_FAILED = "failed"
_FINAL_STOPPED = "stopped"

_STARTUP_JOIN_TIMEOUT = 5.0

_STRIX_AVAILABLE = False
GoTuiRuntime: Any = None
AgentCoordinator: Any = None
run_strix_scan: Any = None
ReportState: Any = None
set_global_report_state: Any = None
TuiLiveView: Any = None
infer_target_type: Any = None
assign_workspace_subdirs: Any = None
collect_local_sources: Any = None
clone_repository: Any = None
resolve_diff_scope_context: Any = None
rewrite_localhost_targets: Any = None
build_diff_scope_instruction: Any = None
DiffScopeResult: Any = None
RepoDiffScope: Any = None
_load_settings: Any = None
_run_dir_for: Any = None
send_user_message_to_agent: Any = None

try:
    from strix.config import load_settings as _ls
    from strix.core.agents import AgentCoordinator as _AC
    from strix.core.runner import run_strix_scan as _rss
    from strix.interface.tui.live_view import TuiLiveView as _TLV
    from strix.runtime import session_manager
    from strix.interface.utils import (
        assign_workspace_subdirs as _aws,
        infer_target_type as _itt,
        collect_local_sources as _cls,
        clone_repository as _clone,
        resolve_diff_scope_context as _resolve_diff,
        rewrite_localhost_targets as _rewrite,
        build_diff_scope_instruction as _build_diff_instr,
        DiffScopeResult,
        RepoDiffScope,
    )
    from strix.report.state import ReportState as _RS, set_global_report_state as _sgrs
    from strix.report.state import get_global_report_state as _ggrs
    from strix.interface.tui.backend.messages import send_user_message_to_agent as _send_umta
    from strix.core.paths import run_dir_for as _rdir
    from strix.interface.tui.runtime import GoTuiRuntime as _GTR

    AgentCoordinator = _AC
    run_strix_scan = _rss
    ReportState = _RS
    set_global_report_state = _sgrs
    TuiLiveView = _TLV
    infer_target_type = _itt
    assign_workspace_subdirs = _aws
    collect_local_sources = _cls
    clone_repository = _clone
    resolve_diff_scope_context = _resolve_diff
    rewrite_localhost_targets = _rewrite
    build_diff_scope_instruction = _build_diff_instr
    _load_settings = _ls
    _run_dir_for = _rdir
    send_user_message_to_agent = _send_umta
    GoTuiRuntime = _GTR
    _STRIX_AVAILABLE = True
except ImportError:
    pass

_get_report_state = _ggrs if _STRIX_AVAILABLE else (lambda: None)


def _report_md_present(run_name: str) -> bool:
    if not run_name:
        return False
    try:
        md = settings.strix_runs_dir / run_name / "penetration_test_report.md"
        return md.is_file() and md.stat().st_size > 0
    except Exception:
        return False


class StrixRuntimeBridge:

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._coordinator: Any = None
        self._stop_event = threading.Event()
        self._root_agent_id: Optional[str] = None
        self._run_name: Optional[str] = None
        self._start_time: float = 0.0
        self._scan_completed: bool = False
        self._scan_task: Optional[Any] = None
        self._last_error: Optional[str] = None
        self._startup_ready: threading.Event = threading.Event()
        self._startup_error: Optional[str] = None
        self._starting = False
        self._startup_abort: threading.Event = threading.Event()
        self._user_cancelled: bool = False
        self._terminal_kind: Optional[str] = None

        self._live_view: Any = None
        self._lv_lock: threading.Lock = threading.Lock()
        self._seen_versions: dict[str, int] = {}
        self._preferred_agent_id: Optional[str] = None
        self._current_targets: list[str] = []
        self._closed_runs: set[str] = set()

        self._runtime: Any = None
        self._GoTuiRuntime: Any = GoTuiRuntime

    # ── lifecycle ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return _STRIX_AVAILABLE

    @property
    def is_running(self) -> bool:
        if self._scan_completed:
            return False
        if self._starting:
            return True
        if not self._coordinator:
            return self._scan_task is not None
        try:
            statuses = getattr(self._coordinator, "statuses", None)
            if not statuses:
                return self._scan_task is not None
            root = self._root_agent_id
            if root and root in statuses:
                s = str(statuses[root])
                return s in ("running", "waiting")
            return any(str(s) == "running" for s in statuses.values())
        except Exception:
            return self._scan_task is not None

    @property
    def is_actively_working(self) -> bool:
        if self._scan_completed:
            return False
        if not self._coordinator:
            return False
        try:
            statuses = getattr(self._coordinator, "statuses", None)
            if not statuses:
                return False
            root = self._root_agent_id
            if root and root in statuses:
                return str(statuses[root]) == "running"
            return any(str(s) == "running" for s in statuses.values())
        except Exception:
            return False

    @property
    def root_agent_id(self) -> Optional[str]:
        return self._root_agent_id

    @property
    def run_name(self) -> Optional[str]:
        return self._run_name

    @property
    def elapsed(self) -> float:
        if self._start_time == 0.0:
            return 0.0
        return time.time() - self._start_time

    # ── start / stop ───────────────────────────────────────────

    def start_scan(
        self,
        targets: list[str],
        instruction: str = "",
        scan_mode: str = "deep",
        scope_mode: str = "auto",
        diff_base: Optional[str] = None,
        local_sources: Optional[list[dict[str, str]]] = None,
    ) -> tuple[bool, str]:
        if not _STRIX_AVAILABLE:
            return False, "STRIX no esta instalado (strix package not found)"
        if self.is_running:
            return False, "Ya hay un escaneo en ejecucion"

        run_name = f"scan-{uuid.uuid4().hex[:8]}"
        try:
            targets_info = self._build_targets_info(targets)
        except ValueError as exc:
            return False, f"Objetivo inválido: {exc}"

        strix_sources: list[dict] = []
        if collect_local_sources:
            try:
                strix_sources = collect_local_sources(targets_info)
            except Exception as exc:
                logger.warning("collect_local_sources failed: %s", exc)

        seen_paths: set[str] = set()
        merged_sources: list[dict[str, str]] = []
        for s in strix_sources + (local_sources or []):
            sp = s.get("source_path", "")
            if sp not in seen_paths:
                seen_paths.add(sp)
                merged_sources.append(s)

        diff_scope: dict[str, Any] = {"active": False}
        try:
            diff_result = resolve_diff_scope_context(
                merged_sources, scope_mode, diff_base, False,
            )
        except Exception as exc:
            logger.error("resolve_diff_scope_context failed: %s", exc)
            return False, f"Error de scope: {exc}"
        if isinstance(diff_result, DiffScopeResult):
            diff_scope = dict(diff_result.metadata) if diff_result.metadata else {"active": False}
            if diff_result.instruction_block:
                instruction = (
                    f"{diff_result.instruction_block}\n\n{instruction}"
                    if instruction else diff_result.instruction_block
                )

        self._stop_event.clear()
        self._current_targets = list(targets)
        self._root_agent_id = None
        self._run_name = run_name
        self._start_time = time.time()
        self._scan_completed = False
        self._scan_task = None
        self._last_error = None
        self._startup_ready = threading.Event()
        self._startup_abort.clear()
        self._starting = False
        self._startup_error = None
        self._user_cancelled = False
        self._terminal_kind = None
        self._preferred_agent_id = None
        self._live_view = None
        self._coordinator = None
        self._runtime = None
        self._seen_versions.clear()

        args = SimpleNamespace(
            run_name=run_name,
            targets_info=targets_info,
            instruction=instruction or "",
            scan_mode=scan_mode,
            diff_scope=diff_scope,
            scope_mode=scope_mode,
            diff_base=diff_base,
            local_sources=merged_sources,
            user_explicit_instruction="",
            max_budget_usd=None,
            max_turns=None,
            needs_setup=False,
        )

        self._starting = True
        self._startup_abort.clear()
        self._thread = threading.Thread(
            target=self._scan_thread, args=(args,), daemon=True)
        self._thread.start()

        if not self._startup_ready.wait(timeout=5.0):
            return self._abort_startup()
        if self._startup_error:
            return False, self._startup_error
        return True, f"Escaneo iniciado: {run_name}"

    def _abort_startup(self) -> tuple[bool, str]:
        self._startup_abort.set()
        self._startup_error = "STRIX no confirmó el inicio del escaneo (timeout de arranque)"
        if self._loop is not None and not self._loop.is_closed():
            scan_task = self._scan_task
            if scan_task is not None and not scan_task.done():
                try:
                    async def _cancel_startup_task() -> None:
                        if self._scan_task is not None and not self._scan_task.done():
                            self._scan_task.cancel()
                    cancel_future = asyncio.run_coroutine_threadsafe(
                        _cancel_startup_task(), self._loop)
                    cancel_future.result(timeout=2.0)
                except Exception as exc:
                    logger.warning("start_scan: task cancel after startup timeout failed: %s", exc)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_STARTUP_JOIN_TIMEOUT)
            if thread.is_alive():
                self._startup_error = (
                    "STRIX no confirmó el inicio del escaneo y el hilo de arranque "
                    "no terminó; el bridge queda bloqueado hasta reiniciar el servicio"
                )
                logger.error("start_scan: startup thread still alive after join timeout")
                return False, self._startup_error
        return False, "STRIX no confirmó el inicio del escaneo (timeout de arranque)"

    @staticmethod
    def _resolve_image() -> str:
        if _load_settings:
            try:
                image = _load_settings().runtime.image
                if image:
                    return image
            except Exception:
                pass
        return "strix-sandbox:latest"

    @staticmethod
    def _build_targets_info(targets: list[str]) -> list[dict]:
        info: list[dict] = []
        for t in targets:
            t = t.strip()
            if not t:
                continue
            try:
                target_type, target_dict = infer_target_type(t)
                info.append({"type": target_type, "details": target_dict, "original": t})
            except ValueError as exc:
                raise ValueError(
                    f"No se pudo clasificar el objetivo '{t}': {exc}"
                ) from exc
        assign_workspace_subdirs(info)
        return info

    # ── scan thread ────────────────────────────────────────────

    def _scan_thread(self, args: SimpleNamespace) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop

        async def _main() -> None:
            if self._startup_abort.is_set():
                return

            runtime = self._GoTuiRuntime(args)
            self._runtime = runtime
            self._coordinator = runtime.coordinator

            runtime.init_run_state()

            with self._lv_lock:
                self._live_view = runtime.live_view
            self._root_agent_id = None

            runtime.start_scan()
            self._scan_task = runtime.scan_task

            discovery = asyncio.create_task(self._poll_root())
            self._startup_ready.set()

            try:
                if self._scan_task is not None:
                    await self._scan_task
            except asyncio.CancelledError:
                if self._startup_abort.is_set():
                    self._terminal_kind = _FINAL_FAILED
                    self._last_error = self._startup_error or (
                        "Escaneo abortado durante el arranque")
                else:
                    self._user_cancelled = True
                    self._terminal_kind = _FINAL_STOPPED
            except Exception as e:
                self._terminal_kind = _FINAL_FAILED
                self._last_error = str(e)

            self._scan_completed = True
            discovery.cancel()
            try:
                await asyncio.gather(discovery, return_exceptions=True)
            except Exception:
                pass

            if self._terminal_kind is None:
                self._terminal_kind = self._derive_terminal_kind()

            self._persist_final_state()

            if self._terminal_kind == _FINAL_COMPLETED:
                self._emit_event("scan_complete", "", "Escaneo finalizado")
            elif self._terminal_kind == _FINAL_STOPPED:
                self._emit_event("scan_cancelled", "", "Escaneo cancelado")
            else:
                self._emit_event("scan_error", "", self._last_error or "Escaneo terminó con error")

        try:
            loop.run_until_complete(_main())
        except asyncio.CancelledError:
            self._scan_completed = True
            self._last_error = "Escaneo cancelado"
        except Exception as e:
            if not self._scan_completed:
                self._scan_completed = True
                self._last_error = str(e)
                self._startup_error = str(e)
                logger.error("Scan thread crashed before finalizer: %s", e)
            else:
                logger.warning("Post-scan teardown error: %s", e)
        finally:
            self._starting = False
            self._startup_ready.set()
            if self._startup_abort.is_set():
                self._persist_aborted_run()
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _poll_root(self) -> None:
        for _ in range(600):
            parent_of = getattr(self._coordinator, "parent_of", None)
            if parent_of:
                for aid, p in parent_of.items():
                    if p is None:
                        self._root_agent_id = aid
                        return
            await asyncio.sleep(0.1)
        logger.warning("Root agent not discovered within 60s")

    # ── scan state derivation ──────────────────────────────────

    def _derive_terminal_kind(self) -> str:
        root_status = self.get_root_status()
        rs = _get_report_state()
        rr_status = (rs.run_record or {}).get("status") if rs else None

        if root_status == "completed" and rr_status == _FINAL_COMPLETED:
            if _report_md_present(self._run_name or ""):
                return _FINAL_COMPLETED
            return _FINAL_FAILED

        if root_status == "stopped":
            return _FINAL_STOPPED

        if root_status in ("failed", "crashed"):
            return _FINAL_FAILED

        return _FINAL_FAILED

    def _persist_final_state(self) -> None:
        rs = _get_report_state()
        if rs is None:
            return
        try:
            if self._terminal_kind == _FINAL_STOPPED:
                rs.save_run_data(status=_FINAL_STOPPED)
            elif self._terminal_kind == _FINAL_FAILED:
                if self._last_error:
                    try:
                        rs.run_record["error"] = self._last_error
                    except Exception:
                        pass
                rs.save_run_data(status=_FINAL_FAILED)
        except Exception as exc:
            logger.warning("Failed to persist final state for %s: %s",
                           self._run_name, exc)

    def _persist_aborted_run(self) -> None:
        run_name = self._run_name
        if not run_name or _run_dir_for is None:
            return
        rs = _get_report_state()
        if rs is None or getattr(rs, "run_name", None) != run_name:
            return
        try:
            if rs.run_record.get("status") == _FINAL_FAILED:
                return
            run_dir = _run_dir_for(run_name)
            if not (run_dir / "run.json").is_file():
                return
        except Exception:
            return
        try:
            if self._startup_error:
                try:
                    rs.run_record["error"] = self._startup_error
                except Exception:
                    pass
            rs.save_run_data(status=_FINAL_FAILED)
            logger.warning("Startup abort persisted %s as failed", run_name)
        except Exception as exc:
            logger.warning("Failed to persist aborted startup for %s: %s", run_name, exc)

    # ── event emission ─────────────────────────────────────────

    def _emit_event(self, event_type: str, agent_id: str, content: str) -> None:
        rn = self._run_name or ""
        if rn in self._closed_runs and event_type not in ("scan_cancelled",):
            return
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return
            event = {
                "id": f"bridge_{event_type}_{lv._next_event_id}",
                "type": "system",
                "agent_id": agent_id,
                "timestamp": time.time(),
                "version": 0,
                "data": {"event": event_type, "content": content, "run_name": rn},
            }
            lv._next_event_id += 1
            lv.events.append(event)

    # ── bot interface ──────────────────────────────────────────

    def poll_events(self) -> list[dict[str, Any]]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return []
            changed = []
            for ev in lv.events:
                eid = ev.get("id", "")
                version = int(ev.get("version", 0))
                key = f"{eid}:v{version}"
                last_key = self._seen_versions.get(eid, f"{eid}:v{-1}")
                last_v = int(last_key.split(":v")[-1]) if ":v" in last_key else -1
                if version > last_v:
                    changed.append(dict(ev))
                    self._seen_versions[eid] = key
        return changed

    def send_message(self, agent_id: str, text: str) -> bool:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return False
        try:
            return send_user_message_to_agent(
                coordinator=self._coordinator,
                loop=self._loop,
                live_view=self._live_view,
                target_agent_id=agent_id,
                message=text,
            )
        except Exception as exc:
            logger.warning("send_message(%s) failed: %s", agent_id, exc)
            return False

    def send_message_to_agent(self, text: str, agent_id: Optional[str] = None) -> bool:
        aid = agent_id or self._root_agent_id or ""
        if not aid:
            return False
        return self.send_message(aid, text)

    def stop_scan(self) -> bool:
        self._stop_event.set()
        if self._runtime is None:
            return True

        quit_done = False
        if self._loop and not self._loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._runtime.quit(), self._loop)
                future.result(timeout=30)
                quit_done = True
            except Exception as exc:
                logger.warning("stop_scan: quit() failed: %s", exc)

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=20)
        if thread and thread.is_alive():
            logger.error("stop_scan: STRIX thread still alive after join timeout")
            return False

        return quit_done

    def stop_agent(self, agent_id: str) -> bool:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.cancel_descendants_graceful(agent_id), self._loop)
            future.result(timeout=30)
            return True
        except Exception as exc:
            logger.warning("stop_agent(%s) failed: %s", agent_id, exc)
            return False

    def check_waiting_notification(self) -> Optional[dict[str, Any]]:
        if not self._coordinator or self._scan_completed:
            return None

        root = self._root_agent_id
        if not root:
            return None

        if self._loop is None or self._loop.is_closed():
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.wait_kind_of(root), self._loop)
            wait_kind = future.result(timeout=2.0)
        except Exception:
            return None

        if wait_kind != "user":
            return None

        agent_name = ""
        with self._lv_lock:
            lv = self._live_view
            if lv and root in lv.agents:
                agent_name = lv.agents[root].get("name", root)

        return {
            "id": f"bridge_agent_waiting_{root}",
            "type": "system",
            "agent_id": root,
            "timestamp": time.time(),
            "version": 0,
            "data": {
                "event": "agent_waiting",
                "content": agent_name or root,
                "run_name": self._run_name or "",
            },
        }

    def ack_waiting_notification(self) -> None:
        pass

    def get_descendant_status_summary(self) -> dict[str, int]:
        if not self._coordinator:
            return {}
        try:
            statuses = getattr(self._coordinator, "statuses", None)
            parent_of = getattr(self._coordinator, "parent_of", None)
            if not statuses or not parent_of:
                return {}
            root = self._root_agent_id
            counts: dict[str, int] = {}
            for aid, s in statuses.items():
                if aid == root:
                    continue
                current = aid
                is_descendant = False
                for _ in range(20):
                    p = parent_of.get(current)
                    if p == root:
                        is_descendant = True
                        break
                    if p is None or p not in parent_of:
                        break
                    current = p
                if not is_descendant:
                    continue
                key = str(s)
                counts[key] = counts.get(key, 0) + 1
            return counts
        except Exception:
            return {}

    # ── read-only projections ──────────────────────────────────

    def get_agent_tree(self) -> Optional[dict[str, Any]]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return None
            tree: dict[str, Any] = {"agents": {}}
            for aid, info in lv.agents.items():
                tree["agents"][aid] = dict(info)
        return tree

    def list_agents(self) -> list[dict]:
        tree = self.get_agent_tree()
        if not tree:
            return []
        return list(tree["agents"].values())

    def agent_timeline(self, agent_id: str) -> list[dict[str, Any]]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return []
            return list(lv.events_for_agent(agent_id))

    def get_root_status(self) -> str:
        if not self._coordinator:
            return "unknown"
        root = self._root_agent_id
        if not root:
            return "unknown"
        try:
            statuses = getattr(self._coordinator, "statuses", None)
            if not statuses:
                return "unknown"
            return str(statuses.get(root, "unknown"))
        except Exception:
            return "unknown"

    def get_tool_state(self) -> dict[str, Any]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return {
                    "active_count": 0, "completed_count": 0, "failed_count": 0,
                    "current_tool_name": "", "current_tool_args": {},
                    "current_tool_status": "idle", "active_agent_name": "",
                    "streaming": False, "awaiting_input": False, "input_prompt": "",
                }

            running_tools: list[dict] = []
            completed = 0
            failed = 0
            for ev in lv.events:
                if ev.get("type") != "tool":
                    continue
                data = ev.get("data", {})
                status = data.get("status", "")
                if status == "running":
                    running_tools.append(data)
                elif status == "completed":
                    completed += 1
                elif status == "failed":
                    failed += 1

            current_tool = running_tools[-1] if running_tools else None
            agent_name = ""
            if current_tool:
                agent_id = current_tool.get("agent_id", "")
                if agent_id in lv.agents:
                    agent_name = lv.agents[agent_id].get("name", agent_id)[:8]

            streaming = any(
                ev.get("type") == "chat"
                and ev.get("data", {}).get("metadata", {}).get("streaming")
                for ev in reversed(lv.events)
            )

            root = self._root_agent_id
            is_waiting = False
            if self._coordinator and root:
                try:
                    statuses = getattr(self._coordinator, "statuses", None)
                    if statuses and root in statuses:
                        is_waiting = str(statuses[root]) == "waiting"
                except Exception:
                    pass

        return {
            "active_count": len(running_tools),
            "completed_count": completed, "failed_count": failed,
            "current_tool_name": current_tool["tool_name"] if current_tool else "",
            "current_tool_args": current_tool.get("args", {}) if current_tool else {},
            "current_tool_status": "running" if current_tool else "idle",
            "active_agent_name": agent_name, "streaming": streaming,
            "awaiting_input": is_waiting, "input_prompt": "",
        }

    def get_vulnerabilities(self) -> list[dict[str, Any]]:
        rs = _get_report_state()
        if rs is None:
            return []
        return list(rs.vulnerability_reports)

    def get_run_status(self) -> dict:
        status: dict[str, Any] = {
            "run_name": self._run_name, "is_running": self.is_running,
            "elapsed": self.elapsed, "mode": "unknown", "phase": "running",
            "error": None,
        }
        if self._run_name:
            run_dir = settings.strix_runs_dir / self._run_name
            run_json = run_dir / "run.json"
            if run_json.exists():
                try:
                    import json
                    data = json.loads(run_json.read_text())
                    status["mode"] = data.get("scan_mode", "unknown")
                    status["phase"] = data.get("status", "running")
                except (json.JSONDecodeError, OSError):
                    pass
            status["run_dir"] = str(run_dir)
        return status

    def to_status_dict(self) -> dict[str, Any]:
        status = self.get_run_status()
        root_status = self.get_root_status()
        phase = root_status if root_status in ("running", "waiting",
                                               "completed", "failed",
                                               "stopped", "initializing") else "running"

        state: dict[str, Any] = {
            "run_name": status.get("run_name", "pending"),
            "target": self._current_targets,
            "mode": status.get("mode", "deep"),
            "phase": phase,
            "elapsed": _fmt_duration(status["elapsed"]),
            "error": self._last_error,
            "is_active": self.is_running,
            "awaiting_input": (root_status == "waiting"),
            "input_prompt": "",
        }

        if not self.is_running:
            state["is_active"] = False

        return state

    def cleanup(self) -> None:
        self._stop_event.set()
        if self._runtime is not None and self._loop and not self._loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._runtime.quit(), self._loop)
                future.result(timeout=30)
            except Exception:
                pass
        self._scan_completed = True


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
