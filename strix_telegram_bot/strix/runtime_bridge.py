"""StrixRuntimeBridge — thin projection of the official Strix 1.5 TUI for Telegram.

Delegates lifecycle to GoTuiRuntime WITHOUT starting the Go sidecar:
  - GoTuiRuntime creates coordinator, live_view, controller, report_state
  - bridge reuses init_run_state() + start_scan() for setup
  - Agent data via coordinator.graph_snapshot() (parent_of, statuses, names, errors)
  - Events via live_view.events (populated by capture_event)
  - AWAITING_USER requires both coordinator.wait_kind_of == "user" AND status == "waiting"
  - Lifecycle driven by scan_task.done(), NOT root agent status
  - Cleanup via GoTuiRuntime.quit()

No parallel event queue. No duplicate state. No synthetic lifecycle events.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_FINAL_COMPLETED = "completed"
_FINAL_FAILED = "failed"
_FINAL_STOPPED = "stopped"

_STARTUP_JOIN_TIMEOUT = 5.0

SPANISH_DIRECTIVE = (
    "Todas las comunicaciones dirigidas al usuario deben estar en español.\n"
    "El informe final y su narrativa deben estar en español.\n"
    "Conserva literalmente código, comandos, URLs, endpoints, payloads, "
    "encabezados, rutas, nombres de herramientas, CVE/CWE e identificadores técnicos."
)


@dataclass
class ScanContext:
    original_instruction: str
    effective_strix_instruction: str = field(default="")

    def __post_init__(self) -> None:
        if not self.effective_strix_instruction:
            self.effective_strix_instruction = (
                SPANISH_DIRECTIVE + "\n\n" + self.original_instruction
            )


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
prepare_run: Any = None
_build_targets_info_official: Any = None

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
    from strix.interface.scan_setup import prepare_run as _prepare_run
    from strix.interface.scan_setup import build_targets_info as _bti

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
    prepare_run = _prepare_run
    _build_targets_info_official = _bti
    _STRIX_AVAILABLE = True
except ImportError:
    pass

_get_report_state = _ggrs if _STRIX_AVAILABLE else (lambda: None)

DEFAULT_MAX_TURNS: int = 500
try:
    from strix.config.settings import DEFAULT_MAX_TURNS as _DMT
    DEFAULT_MAX_TURNS = _DMT
except ImportError:
    pass


def _normalize_max_turns(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_MAX_TURNS


def _report_md_present(run_name: str) -> bool:
    if not run_name:
        return False
    try:
        run_dir = _run_dir_for(run_name)
        md = run_dir / "penetration_test_report.md"
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

    # ── per-run report state (ownership) ───────────────────────
    #
    # The ONLY authority for the active run's state is the per-run
    # ReportState owned by GoTuiRuntime (created during init_run_state()).
    # The module-level global report state must never be used to decide
    # lifecycle, vulnerabilities, or persistence of the active run.

    def _report_state(self) -> Any:
        """Return the current GoTuiRuntime per-run ReportState (or None)."""
        runtime = self._runtime
        if runtime is None:
            return None
        rs = getattr(runtime, "report_state", None)
        if rs is None:
            return None
        # Guard against a per-run state belonging to a different run than the
        # one this bridge is currently shepherding.
        if getattr(rs, "run_name", None) is not None and self._run_name is not None:
            if rs.run_name != self._run_name:
                return None
        return rs

    # ── lifecycle ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return _STRIX_AVAILABLE

    @property
    def is_running(self) -> bool:
        if self._startup_abort.is_set():
            return False
        if self._starting:
            return True
        if self._scan_task is not None and not self._scan_task.done():
            return True
        if self._scan_task is not None and self._scan_task.done():
            if self._last_error is not None:
                return False
            if self._runtime is not None:
                ctrl = getattr(self._runtime, "controller", None)
                if ctrl is not None:
                    scan_state = getattr(ctrl, "scan_state", None)
                    if scan_state in ("running", "waiting"):
                        return True
            rs = self._report_state()
            if rs is not None and rs.run_record:
                rr_status = rs.run_record.get("status")
                if rr_status in ("running", "waiting"):
                    return True
            return False
        return False

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
        # Deterministic guard: never start a 2nd run while the previous thread is alive
        if self._thread is not None and self._thread.is_alive():
            return False, "El escaneo anterior todavía está finalizando"

        ctx = ScanContext(original_instruction=instruction or "")
        run_name = f"scan-{uuid.uuid4().hex[:8]}"
        try:
            if _build_targets_info_official is not None:
                _ns = SimpleNamespace(target=list(targets), target_list=[])
                _build_targets_info_official(_ns)
                targets_info = _ns.targets_info
            else:
                targets_info = []
        except Exception as exc:
            return False, f"Objetivo inválido: {exc}"

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
            instruction=ctx.effective_strix_instruction,
            scan_mode=scan_mode,
            diff_scope={"active": False},
            scope_mode=scope_mode,
            diff_base=diff_base,
            local_sources=list(local_sources or []),
            user_explicit_instruction="",
            max_budget_usd=None,
            max_turns=_normalize_max_turns(DEFAULT_MAX_TURNS),
            needs_setup=False,
            workspace_mount=None,
            resume=None,
            non_interactive=False,
            user_instruction=ctx.original_instruction,
        )

        self._starting = True
        self._startup_abort.clear()
        self._thread = threading.Thread(
            target=self._scan_thread, args=(args, local_sources), daemon=True)
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

    # ── scan thread ────────────────────────────────────────────

    def _scan_thread(self, args: SimpleNamespace, user_local_sources: Optional[list[dict[str, str]]] = None) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop

        async def _main() -> None:
            if self._startup_abort.is_set():
                self._scan_completed = True
                return

            args.max_turns = _normalize_max_turns(args.max_turns)

            if prepare_run is not None:
                try:
                    prepare_run(args)
                except Exception as exc:
                    logger.error("prepare_run failed: %s", exc)
                    self._startup_error = f"Preparación del escaneo falló: {exc}"
                    self._scan_completed = True
                    return
                self._run_name = args.run_name
                if args.local_sources is None:
                    args.local_sources = []
                for s in (user_local_sources or []):
                    sp = s.get("source_path", "")
                    if sp and not any(x.get("source_path") == sp for x in args.local_sources):
                        args.local_sources.append(s)

            runtime = self._GoTuiRuntime(args)
            if self._startup_abort.is_set():
                self._scan_completed = True
                return
            self._runtime = runtime
            self._coordinator = runtime.coordinator

            runtime.init_run_state()
            if self._startup_abort.is_set():
                self._scan_completed = True
                return

            with self._lv_lock:
                self._live_view = runtime.live_view
            self._root_agent_id = None

            runtime.start_scan()
            self._scan_task = runtime.scan_task
            if self._startup_abort.is_set():
                if self._scan_task is not None and not self._scan_task.done():
                    self._scan_task.cancel()
                self._scan_completed = True
                return

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
        rs = self._report_state()
        rr_status = (rs.run_record or {}).get("status") if rs else None

        if root_status == "completed" and rr_status == _FINAL_COMPLETED:
            if _report_md_present(self._run_name or ""):
                return _FINAL_COMPLETED
            return _FINAL_FAILED

        if root_status == "stopped":
            return _FINAL_STOPPED

        if root_status in ("failed", "crashed"):
            error_msg = ""
            if self._runtime is not None:
                ctrl = getattr(self._runtime, "controller", None)
                if ctrl is not None:
                    error_msg = getattr(ctrl, "error", "") or ""
            if "context" in error_msg.lower() and "size" in error_msg.lower():
                logger.error(
                    "ROOT FAILED BEFORE FINISH_SCAN → OFFICIAL REPORT NOT PRODUCED. "
                    "Context size exceeded. Root agent death means finish_scan never ran."
                )
            else:
                logger.error(
                    "ROOT FAILED BEFORE FINISH_SCAN → OFFICIAL REPORT NOT PRODUCED. "
                    "Root agent death means finish_scan never ran. Error: %s",
                    error_msg or "unknown"
                )
            return _FINAL_FAILED

        return _FINAL_FAILED

    def _persist_final_state(self) -> None:
        rs = self._report_state()
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
        rs = self._report_state()
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

    def stop_scan_async(self, on_done: Optional[Callable[[bool], None]] = None) -> bool:
        """Run stop_scan in a background thread so the polling loop is not blocked.

        Returns True if a stop was initiated. The (possibly slow) quit()+join()
        happens off the caller's thread; the honest result is reported via on_done.
        """
        if not self.is_running:
            return False

        def _worker() -> None:
            ok = self.stop_scan()
            logger.info("stop_scan_async: completed (ok=%s)", ok)
            if on_done is not None:
                try:
                    on_done(ok)
                except Exception as exc:
                    logger.warning("stop_scan_async: on_done callback failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()
        return True

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

        if self._loop is None or self._loop.is_closed():
            return None

        statuses = getattr(self._coordinator, "statuses", None)
        if not statuses:
            return None

        for agent_id in list(statuses.keys()):
            agent_status = str(statuses.get(agent_id, ""))
            if agent_status != "waiting":
                continue

            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._coordinator.wait_kind_of(agent_id), self._loop)
                wait_kind = future.result(timeout=2.0)
            except Exception:
                continue

            if wait_kind != "user":
                continue

            agent_name = ""
            with self._lv_lock:
                lv = self._live_view
                if lv and hasattr(lv, "agents") and agent_id in lv.agents:
                    agent_name = lv.agents[agent_id].get("name", agent_id)

            return {
                "id": f"bridge_agent_waiting_{agent_id}",
                "type": "system",
                "agent_id": agent_id,
                "timestamp": time.time(),
                "version": 0,
                "data": {
                    "event": "agent_waiting",
                    "content": agent_name or agent_id,
                    "run_name": self._run_name or "",
                },
            }

        return None

    def ack_waiting_notification(self) -> None:
        pass

    def awaiting_user_agents(self) -> list[dict[str, Any]]:
        """Agents with status == 'waiting' AND wait_kind == 'user'.

        Only these agents open the user reply channel. wait_kind 'agents'
        (waiting for children to finish) and 'stalled' do NOT.
        """
        if not self._coordinator or self._scan_completed:
            return []
        if self._loop is None or self._loop.is_closed():
            return []
        statuses = getattr(self._coordinator, "statuses", None)
        if not statuses:
            return []
        result: list[dict[str, Any]] = []
        for agent_id in list(statuses.keys()):
            if str(statuses.get(agent_id, "")) != "waiting":
                continue
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._coordinator.wait_kind_of(agent_id), self._loop)
                wait_kind = future.result(timeout=2.0)
            except Exception:
                continue
            if wait_kind != "user":
                continue
            name = agent_id
            with self._lv_lock:
                lv = self._live_view
                if lv is not None and hasattr(lv, "agents") and agent_id in lv.agents:
                    name = lv.agents[agent_id].get("name", agent_id)
            result.append({"id": agent_id, "name": name})
        return result

    def last_agent_message(self, agent_id: str) -> str:
        """Last assistant chat content from the agent's timeline (for prompts)."""
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return ""
            try:
                events = list(lv.events_for_agent(agent_id))
            except Exception:
                return ""
        for ev in reversed(events):
            if ev.get("type") != "chat":
                continue
            data = ev.get("data", {})
            if data.get("role") != "assistant":
                continue
            content = data.get("content", "")
            if content:
                return content
        return ""

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
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.graph_snapshot(), self._loop)
            result = future.result(timeout=3.0)
        except Exception:
            return None
        parent_of, statuses, names = result[0], result[1], result[2]
        errors = result[3] if len(result) > 3 else {}
        tree: dict[str, Any] = {"agents": {}}
        for aid in statuses:
            tree["agents"][aid] = {
                "id": aid,
                "name": names.get(aid, aid),
                "status": str(statuses[aid]),
                "parent_id": parent_of.get(aid),
                "error": errors.get(aid) if errors else None,
            }
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

            is_waiting = False
            if self._coordinator and self._loop and not self._loop.is_closed():
                try:
                    statuses = getattr(self._coordinator, "statuses", None)
                    if statuses:
                        for agent_id, s in statuses.items():
                            if str(s) != "waiting":
                                continue
                            try:
                                future = asyncio.run_coroutine_threadsafe(
                                    self._coordinator.wait_kind_of(agent_id), self._loop)
                                wk = future.result(timeout=1.0)
                            except Exception:
                                continue
                            if wk == "user":
                                is_waiting = True
                                break
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
        rs = self._report_state()
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
            run_dir = _run_dir_for(self._run_name)
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

        phase = "running"
        if self._runtime is not None:
            ctrl = getattr(self._runtime, "controller", None)
            if ctrl is not None:
                scan_state = getattr(ctrl, "scan_state", None)
                if scan_state:
                    phase = scan_state
        if phase == "running":
            rs = self._report_state()
            if rs is not None and rs.run_record:
                rr_status = rs.run_record.get("status")
                if rr_status in ("completed", "failed", "stopped"):
                    phase = rr_status
        if phase == "running" and self._last_error:
            phase = "failed"

        awaiting_input = False
        if self._coordinator and self._loop and not self._loop.is_closed():
            try:
                statuses = getattr(self._coordinator, "statuses", None)
                if statuses:
                    for agent_id, s in statuses.items():
                        if str(s) != "waiting":
                            continue
                        try:
                            future = asyncio.run_coroutine_threadsafe(
                                self._coordinator.wait_kind_of(agent_id), self._loop)
                            wk = future.result(timeout=1.0)
                        except Exception:
                            continue
                        if wk == "user":
                            awaiting_input = True
                            break
            except Exception:
                pass

        state: dict[str, Any] = {
            "run_name": status.get("run_name", "pending"),
            "target": self._current_targets,
            "mode": status.get("mode", "deep"),
            "phase": phase,
            "elapsed": _fmt_duration(status["elapsed"]),
            "error": self._last_error,
            "is_active": self.is_running,
            "awaiting_input": awaiting_input,
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
