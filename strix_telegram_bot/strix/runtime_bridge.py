"""StrixRuntimeBridge — asyncio thread wrapping AgentCoordinator + run_strix_scan.

TuiLiveView is the single source of truth for all scan events.
No parallel event queue or duplicate state — everything flows through
the same TuiLiveView instance that the official TUI uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from strix_telegram_bot.config import settings

logger = logging.getLogger(__name__)

_STRIX_AVAILABLE = False
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
    _STRIX_AVAILABLE = True
except ImportError:
    pass


class StrixRuntimeBridge:

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._coordinator: Any = None
        self._scan_future: Optional[Any] = None
        self._stop_event = threading.Event()
        self._root_agent_id: Optional[str] = None
        self._run_name: Optional[str] = None
        self._scan_image: str = ""
        self._start_time: float = 0.0
        self._scan_completed: bool = False
        self._awaiting_input: bool = False
        self._input_prompt: str = ""
        self._phase: str = "running"
        self._last_error: Optional[str] = None
        self._scan_task: Optional[Any] = None
        self._scan_status: str = "unknown"
        self._closed_runs: set[str] = set()
        self._current_targets: list[str] = []

        self._live_view: Any = None
        self._lv_lock: threading.Lock = threading.Lock()
        self._last_event_index: int = 0
        self._waiting_notified: bool = False
        self._preferred_agent_id: Optional[str] = None

    # ── lifecycle ──────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return _STRIX_AVAILABLE

    @property
    def is_running(self) -> bool:
        return self._scan_status in ("initializing", "running", "waiting")

    @property
    def is_actively_working(self) -> bool:
        return self._scan_status in ("initializing", "running")

    @property
    def scan_status(self) -> str:
        return self._scan_status

    @property
    def run_name(self) -> Optional[str]:
        return self._run_name

    @property
    def root_agent_id(self) -> Optional[str]:
        return self._root_agent_id

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
        non_interactive: bool = False,
        image: Optional[str] = None,
        local_sources: Optional[list[dict[str, str]]] = None,
    ) -> tuple[bool, str]:
        if not _STRIX_AVAILABLE:
            return False, "STRIX no esta instalado (strix package not found)"
        if self.is_running:
            return False, "Ya hay un escaneo en ejecucion"

        run_name = f"scan-{uuid.uuid4().hex[:8]}"
        targets_info = self._build_targets_info(targets)

        diff_scope: dict[str, Any] = {"active": False, "diff_base": None}
        if scope_mode == "diff" and diff_base:
            try:
                diff_result = resolve_diff_scope_context(targets_info, diff_base)
                if isinstance(diff_result, DiffScopeResult):
                    diff_scope = {
                        "active": True,
                        "diff_base": diff_base,
                        "changed_files": diff_result.changed_files,
                        "instruction": diff_result.instruction,
                    }
                    if diff_result.instruction:
                        instruction = f"{instruction}\n\n{diff_result.instruction}" if instruction else diff_result.instruction
                elif isinstance(diff_result, RepoDiffScope):
                    diff_scope = {
                        "active": True,
                        "diff_base": diff_base,
                        "changed_files": diff_result.changed_files,
                        "instruction": diff_result.instruction,
                    }
                    if diff_result.instruction:
                        instruction = f"{instruction}\n\n{diff_result.instruction}" if instruction else diff_result.instruction
            except Exception as exc:
                logger.warning("resolve_diff_scope_context failed: %s", exc)
                diff_scope = {"active": True, "diff_base": diff_base}
        elif scope_mode == "auto":
            diff_scope = {"active": True, "diff_base": diff_base or "auto"}

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

        scan_config: dict[str, Any] = {
            "scan_id": run_name,
            "targets": targets_info,
            "user_instructions": instruction,
            "run_name": run_name,
            "scan_mode": scan_mode,
            "diff_scope": diff_scope,
            "scope_mode": scope_mode,
            "diff_base": diff_base,
            "non_interactive": non_interactive,
            "local_sources": merged_sources,
            "resume_instruction": "",
        }

        self._stop_event.clear()
        self._current_targets = list(targets)
        self._coordinator = AgentCoordinator()
        self._root_agent_id = None
        self._run_name = run_name
        self._scan_image = image or self._resolve_image()
        self._start_time = time.time()
        self._scan_completed = False
        self._awaiting_input = False
        self._input_prompt = ""
        self._phase = "running"
        self._last_error = None
        self._scan_task = None
        self._scan_status = "initializing"
        self._preferred_agent_id = None

        self._live_view = TuiLiveView()
        self._last_event_index = 0
        self._waiting_notified = False

        self._thread = threading.Thread(
            target=self._scan_thread,
            args=(scan_config, merged_sources),
            daemon=True,
        )
        self._thread.start()

        if self._last_error:
            return False, self._last_error

        return True, f"Escaneo iniciado: {run_name}"

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
                info.append(
                    {"type": target_type, "details": target_dict, "original": t}
                )
            except ValueError:
                info.append(
                    {
                        "type": "web_application",
                        "details": {"target_url": f"https://{t}"},
                        "original": t,
                    }
                )
        assign_workspace_subdirs(info)
        return info

    # ── scan thread (asyncio) ──────────────────────────────────

    def _scan_thread(self, scan_config: dict, local_sources: list[dict]) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop

        async def _poll_root() -> None:
            for _ in range(600):
                parent_of = getattr(self._coordinator, "parent_of", None)
                if parent_of:
                    for aid, p in parent_of.items():
                        if p is None:
                            self._root_agent_id = aid
                            self._scan_status = "running"
                            with self._lv_lock:
                                self._live_view.upsert_agent(aid, status="running")
                            self._emit_event("root_discovered", aid, f"Agente raiz: {aid}")
                            return
                await asyncio.sleep(0.1)
            logger.warning("Root agent not discovered within 60s")

        async def _poll_status() -> None:
            while not self._scan_completed:
                await asyncio.sleep(1.0)
                try:
                    statuses = getattr(self._coordinator, "statuses", None)
                    if not statuses:
                        continue
                    root = self._root_agent_id
                    any_running = any(str(s) == "running" for s in statuses.values())
                    root_waiting = root is not None and str(statuses.get(root, "")) == "waiting"

                    if any_running:
                        self._scan_status = "running"
                        self._awaiting_input = False
                        self._waiting_notified = False
                    elif root_waiting:
                        was_already_waiting = self._scan_status == "waiting"
                        self._scan_status = "waiting"
                        self._awaiting_input = True
                        if not was_already_waiting and not self._waiting_notified:
                            self._waiting_notified = True
                            self._notify_agent_waiting(root)
                except Exception:
                    pass

        non_interactive = bool(scan_config.get("non_interactive", False))

        async def _run_scan() -> Any:
            rs = ReportState(run_name=self._run_name)
            set_global_report_state(rs)
            rs.set_scan_config(scan_config)

            live_view: Any = self._live_view
            current_run = self._run_name

            def bound_event_sink(agent_id: str, event: Any) -> None:
                with self._lv_lock:
                    live_view.ingest_sdk_event(agent_id, event)

            return await run_strix_scan(
                scan_config=scan_config,
                scan_id=current_run,
                image=self._scan_image,
                local_sources=scan_config.get("local_sources"),
                coordinator=self._coordinator,
                interactive=not non_interactive,
                continuous_interactive=not non_interactive,
                event_sink=bound_event_sink,
            )

        async def _main() -> None:
            self._scan_task = asyncio.create_task(_run_scan())
            discovery = asyncio.create_task(_poll_root())
            status_poller = asyncio.create_task(_poll_status())

            try:
                await self._scan_task
            except asyncio.CancelledError:
                self._scan_status = "stopped"
                self._phase = "stopped"
                self._scan_completed = True
                self._emit_event("scan_cancelled", "", "Escaneo cancelado")
                return
            except Exception as e:
                self._scan_status = "failed"
                self._phase = "failed"
                self._scan_completed = True
                self._last_error = str(e)
                self._emit_event("scan_error", "", f"Error en escaneo: {e}")
                return
            finally:
                status_poller.cancel()
                discovery.cancel()
                try:
                    await asyncio.gather(status_poller, discovery, return_exceptions=True)
                except Exception:
                    pass

            # Scan completed successfully — cleanup before announcing
            current_run = self._run_name or ""
            cleanup_error = None
            if current_run:
                try:
                    await session_manager.cleanup(current_run)
                    logger.info("Sandbox cleaned up for run %s", current_run)
                except Exception as exc:
                    logger.warning("session_manager.cleanup failed for %s: %s", current_run, exc)
                    cleanup_error = str(exc)

            self._scan_status = "completed"
            self._phase = "completed"
            self._scan_completed = True
            self._emit_event("scan_complete", "", "Escaneo finalizado")

            if cleanup_error:
                logger.warning(
                    "Scan %s completed but sandbox cleanup failed: %s",
                    current_run, cleanup_error,
                )

        try:
            loop.run_until_complete(_main())
        except asyncio.CancelledError:
            self._scan_status = "stopped"
            self._phase = "stopped"
            self._scan_completed = True
            self._emit_event("scan_cancelled", "", "Escaneo cancelado")
        except Exception as e:
            if self._scan_status != "completed":
                self._scan_status = "failed"
                self._phase = "failed"
                self._scan_completed = True
                self._last_error = str(e)
                self._emit_event("scan_error", "", f"Error en escaneo: {e}")
            else:
                logger.warning("Post-scan teardown error (scan was completed): %s", e)
        finally:
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

    # ── event emission (synthetic bridge events) ───────────────

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
                "data": {
                    "event": event_type,
                    "content": content,
                    "run_name": rn,
                },
            }
            lv._next_event_id += 1
            lv.events.append(event)

    def _notify_agent_waiting(self, agent_id: str) -> None:
        agent_name = ""
        with self._lv_lock:
            lv = self._live_view
            if lv and agent_id in lv.agents:
                agent_name = lv.agents[agent_id].get("name", agent_id)
        self._emit_event(
            "agent_waiting",
            agent_id,
            agent_name or agent_id,
        )

    # ── bot interface (synchronous, called from drain thread) ──

    def poll_events(self) -> list[dict[str, Any]]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return []
            events = list(lv.events[self._last_event_index:])
            if events:
                self._last_event_index = len(lv.events)
        return events

    def send_message(self, agent_id: str, text: str) -> bool:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return False

        with self._lv_lock:
            lv = self._live_view
            if lv:
                lv.record_user_message(agent_id, text)

        message = {"from": "user", "content": text, "type": "instruction"}
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.send(agent_id, message),
                self._loop,
            )
            result = future.result(timeout=30)
            logger.debug("send_message(%s): result=%s", agent_id, result)
            return bool(result)
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
        current_run = self._run_name or ""
        self._closed_runs.add(current_run)
        cancel_failed = False
        aid = self._root_agent_id or ""
        if self._coordinator and self._loop and not self._loop.is_closed() and aid:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._coordinator.cancel_descendants_graceful(aid),
                    self._loop,
                )
                future.result(timeout=30)
                logger.info("stop_scan: agents cancelled gracefully")
            except Exception as exc:
                logger.warning("stop_scan: graceful cancel failed: %s", exc)
                cancel_failed = True
        if self._loop and not self._loop.is_closed() and self._scan_task is not None:
            try:
                async def _cancel_task() -> None:
                    if self._scan_task and not self._scan_task.done():
                        self._scan_task.cancel()
                cancel_future = asyncio.run_coroutine_threadsafe(
                    _cancel_task(),
                    self._loop,
                )
                cancel_future.result(timeout=10)
                logger.info("stop_scan: scan task cancelled")
            except Exception as exc:
                logger.warning("stop_scan: task cancel failed: %s", exc)
                cancel_failed = True

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=20)
        if thread and thread.is_alive():
            logger.error("stop_scan: STRIX thread still alive after join timeout")
            cancel_failed = True

        return not cancel_failed

    # ── read-only state (consumed by bot) ──────────────────────

    def get_agent_tree(self) -> Optional[dict[str, Any]]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return None
            tree: dict[str, Any] = {"agents": {}}
            for aid, info in lv.agents.items():
                tree["agents"][aid] = dict(info)
        return tree

    def get_tool_state(self) -> dict[str, Any]:
        with self._lv_lock:
            lv = self._live_view
            if lv is None:
                return {
                    "active_count": 0,
                    "completed_count": 0,
                    "failed_count": 0,
                    "current_tool_name": "",
                    "current_tool_args": {},
                    "current_tool_status": "idle",
                    "active_agent_name": "",
                    "streaming": False,
                    "awaiting_input": self._awaiting_input,
                    "input_prompt": self._input_prompt,
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

        return {
            "active_count": len(running_tools),
            "completed_count": completed,
            "failed_count": failed,
            "current_tool_name": current_tool["tool_name"] if current_tool else "",
            "current_tool_args": current_tool.get("args", {}) if current_tool else {},
            "current_tool_status": "running" if current_tool else "idle",
            "active_agent_name": agent_name,
            "streaming": streaming,
            "awaiting_input": self._awaiting_input,
            "input_prompt": self._input_prompt,
        }

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
            events = list(lv.events_for_agent(agent_id))
        return events

    def stop_agent(self, agent_id: str) -> bool:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.cancel_descendants_graceful(agent_id),
                self._loop,
            )
            future.result(timeout=30)
            logger.info("stop_agent(%s): cancelled gracefully", agent_id)
            return True
        except Exception as exc:
            logger.warning("stop_agent(%s) failed: %s", agent_id, exc)
            return False

    def get_vulnerabilities(self) -> list[dict[str, Any]]:
        from strix.report.state import get_global_report_state
        rs = get_global_report_state()
        if rs is None:
            return []
        return list(rs.vulnerability_reports)

    def get_run_status(self) -> dict:
        status: dict[str, Any] = {
            "run_name": self._run_name,
            "is_running": self.is_running,
            "elapsed": self.elapsed,
            "mode": "unknown",
            "phase": "running",
            "error": None,
        }
        if self._run_name:
            run_dir = settings.strix_runs_dir / self._run_name
            run_json = run_dir / "run.json"
            if run_json.exists():
                try:
                    data = json.loads(run_json.read_text())
                    status["mode"] = data.get("scan_mode", "unknown")
                    status["phase"] = data.get("status", "running")
                except (json.JSONDecodeError, OSError):
                    pass
            status["run_dir"] = str(run_dir)
        return status

    def to_status_dict(self) -> dict[str, Any]:
        status = self.get_run_status()

        phase = self._phase
        if self._scan_status == "initializing":
            phase = "initializing"
        elif not status.get("is_running") and not status.get("is_active"):
            if not self._last_error and self._phase == "running":
                phase = "completed"

        state: dict[str, Any] = {
            "run_name": status.get("run_name", "pending"),
            "target": self._current_targets,
            "mode": status.get("mode", "deep"),
            "phase": phase,
            "elapsed": _fmt_duration(status["elapsed"]),
            "error": self._last_error,
            "is_active": self.is_running,
            "awaiting_input": self._awaiting_input,
            "input_prompt": self._input_prompt,
        }

        if not status.get("is_running") and not status.get("is_active"):
            state["is_active"] = False

        return state

    def cleanup(self) -> None:
        self._stop_event.set()
        aid = self._root_agent_id or ""
        if self._coordinator and self._loop and not self._loop.is_closed() and aid:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._coordinator.cancel_descendants_graceful(aid),
                    self._loop,
                )
                future.result(timeout=30)
            except Exception:
                pass
        if self._loop and not self._loop.is_closed() and self._scan_task is not None:
            try:
                async def _cancel_task() -> None:
                    if self._scan_task and not self._scan_task.done():
                        self._scan_task.cancel()
                cancel_future = asyncio.run_coroutine_threadsafe(
                    _cancel_task(),
                    self._loop,
                )
                cancel_future.result(timeout=10)
            except Exception:
                pass
        self._scan_completed = True
        self._scan_status = "stopped"


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
