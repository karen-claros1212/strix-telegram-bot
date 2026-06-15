"""StrixRuntimeBridge — asyncio thread wrapping AgentCoordinator + run_strix_scan."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
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
infer_target_type: Any = None
assign_workspace_subdirs: Any = None
_load_settings: Any = None

try:
    from strix.config import load_settings as _ls
    from strix.core.agents import AgentCoordinator as _AC
    from strix.core.runner import run_strix_scan as _rss
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


_MAX_EVENTS = 500


class ScanEvent:
    type: str
    agent_id: str
    content: str
    timestamp: float
    awaiting_input: bool
    prompt: str

    __slots__ = ("type", "agent_id", "content", "timestamp", "awaiting_input", "prompt")

    def __init__(self, type: str = "", agent_id: str = "", content: str = "",
                 timestamp: float = 0.0, awaiting_input: bool = False,
                 prompt: str = "") -> None:
        self.type = type
        self.agent_id = agent_id
        self.content = content
        self.timestamp = timestamp
        self.awaiting_input = awaiting_input
        self.prompt = prompt

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "agent_id": self.agent_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "awaiting_input": self.awaiting_input,
            "prompt": self.prompt,
        }


class StrixRuntimeBridge:
    """Bridge between STRIX 1.0.4 async runtime and Telegram bot sync world.

    Owns a daemon thread with an asyncio event loop, creates AgentCoordinator
    and passes it to run_strix_scan(). Provides synchronous interface for
    the bot: start, send_message, stop, poll events, read state.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._coordinator: Any = None
        self._scan_future: Optional[Any] = None
        self._event_queue: queue.Queue[ScanEvent] = queue.Queue(maxsize=_MAX_EVENTS)
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

    @property
    def is_available(self) -> bool:
        return _STRIX_AVAILABLE

    @property
    def is_running(self) -> bool:
        return self._scan_status in ("initializing", "running", "waiting")

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
            return False, "STRIX 1.0.4 no está instalado (strix package not found)"
        if self.is_running:
            return False, "Ya hay un escaneo en ejecución"

        run_name = f"scan-{uuid.uuid4().hex[:8]}"
        targets_info = self._build_targets_info(targets)

        # Resolve diff scope using STRIX official function
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
                    # Append diff instruction to user instruction
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

        # Collect local sources using STRIX official function (expects targets_info format)
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

    def _scan_thread(self, scan_config: dict, local_sources: list[dict]) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _poll_root() -> None:
            for _ in range(600):
                parent_of = getattr(self._coordinator, "parent_of", None)
                if parent_of:
                    for aid, p in parent_of.items():
                        if p is None:
                            self._root_agent_id = aid
                            self._scan_status = "running"
                            self._emit_event("root_discovered", aid, f"Agente raíz: {aid}")
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
                    elif root_waiting:
                        self._scan_status = "waiting"
                        self._awaiting_input = True
                        self._input_prompt = "STRIX espera un mensaje"
                except Exception:
                    pass

        non_interactive = bool(scan_config.get("non_interactive", False))

        async def _run_scan() -> Any:
            rs = ReportState(run_name=self._run_name)
            set_global_report_state(rs)
            rs.set_scan_config(scan_config)

            return await run_strix_scan(
                scan_config=scan_config,
                scan_id=self._run_name,
                image=self._scan_image,
                local_sources=scan_config.get("local_sources"),
                coordinator=self._coordinator,
                interactive=not non_interactive,
                event_sink=self._capture_event,
            )

        async def _main() -> None:
            self._scan_task = asyncio.create_task(_run_scan())
            discovery = asyncio.create_task(_poll_root())
            status_poller = asyncio.create_task(_poll_status())

            result = await self._scan_task

            status_poller.cancel()
            discovery.cancel()
            self._scan_status = "completed"
            self._phase = "completed"
            self._scan_completed = True
            self._emit_event("scan_complete", "", "Escaneo finalizado")

        try:
            self._loop.run_until_complete(_main())
        except asyncio.CancelledError:
            self._scan_status = "stopped"
            self._phase = "stopped"
            self._scan_completed = True
            self._emit_event("scan_cancelled", "", "Escaneo cancelado")
        except Exception as e:
            self._scan_status = "failed"
            self._phase = "failed"
            self._scan_completed = True
            self._last_error = str(e)
            self._emit_event("scan_error", "", f"Error en escaneo: {e}")
        finally:
            for t in asyncio.all_tasks(self._loop):
                t.cancel()
            if self._loop.is_running():
                self._loop.stop()
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    def _capture_event(self, agent_id: str, event: Any) -> None:
        event_type = getattr(event, "type", "")
        now = time.time()

        if event_type == "raw_response_event":
            self._ingest_raw_response(agent_id, event)
            return
        if event_type != "run_item_stream_event":
            return

        item = getattr(event, "item", None)
        item_type = getattr(item, "type", "")
        if item_type == "message_output_item":
            text = self._sdk_message_text(item)
            if text:
                se = ScanEvent(
                    type="agent_message",
                    agent_id=agent_id,
                    content=text,
                    timestamp=now,
                    awaiting_input=False,
                    prompt="",
                )
                self._queue_event(se)
        elif item_type == "tool_call_item":
            tool_data = self._sdk_tool_call_data(item)
            content = json.dumps({
                "tool_name": tool_data["tool_name"],
                "args": tool_data["args"],
            }, ensure_ascii=False)
            se = ScanEvent(
                type="tool_call",
                agent_id=agent_id,
                content=content,
                timestamp=now,
            )
            self._queue_event(se)
        elif item_type == "tool_call_output_item":
            tool_data = self._sdk_tool_output_data(item)
            output_text = self._normalize_output(tool_data["output"])[:500]
            content = json.dumps({
                "tool_name": tool_data["tool_name"],
                "output": output_text,
            }, ensure_ascii=False)
            se = ScanEvent(
                type="tool_output",
                agent_id=agent_id,
                content=content,
                timestamp=now,
            )
            self._queue_event(se)

    def _ingest_raw_response(self, agent_id: str, event: Any) -> None:
        data = getattr(event, "data", None)
        if data is None:
            return
        data_type = getattr(data, "type", "")
        if data_type == "response.output_text.delta":
            delta = getattr(data, "delta", "")
            if delta:
                se = ScanEvent(
                    type="stream_delta",
                    agent_id=agent_id,
                    content=delta,
                    timestamp=time.time(),
                )
                self._queue_event(se)
        elif data_type == "tool_call_cancelled":
            tool_name = getattr(data, "name", "unknown")
            se = ScanEvent(
                type="tool_cancelled",
                agent_id=agent_id,
                content=tool_name,
                timestamp=time.time(),
            )
            self._queue_event(se)

    @staticmethod
    def _raw_field(raw: Any, key: str, default: Any = None) -> Any:
        if isinstance(raw, dict):
            return raw.get(key, default)
        return getattr(raw, key, default)

    def _sdk_message_text(self, item: Any) -> str:
        raw = self._raw_field(item, "raw_item", None)
        content = self._raw_field(raw, "content", [])
        return self._message_content_text(content)

    @staticmethod
    def _message_content_text(content: Any) -> str:
        parts: list[str] = []
        items = content if isinstance(content, list) else [content]
        for part in items:
            if isinstance(part, str):
                parts.append(part)
                continue
            text = StrixRuntimeBridge._raw_field(part, "text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _normalize_output(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        try:
            return json.dumps(raw, ensure_ascii=False, default=str)
        except Exception:
            return str(raw)

    @staticmethod
    def _parse_json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return value if isinstance(value, dict) else {}

    def _sdk_tool_output_data(self, item: Any) -> dict[str, Any]:
        raw = self._raw_field(item, "raw_item", None)
        call_id = str(self._raw_field(raw, "call_id") or self._raw_field(raw, "id") or id(item))
        return {
            "call_id": call_id,
            "tool_name": str(self._raw_field(raw, "name") or self._raw_field(raw, "type") or "tool"),
            "output": getattr(item, "output", self._raw_field(raw, "output")),
        }

    def _sdk_tool_call_data(self, item: Any) -> dict[str, Any]:
        raw = self._raw_field(item, "raw_item", None)
        call_id = str(self._raw_field(raw, "call_id") or self._raw_field(raw, "id") or id(item))
        tool_name = str(
            self._raw_field(raw, "name")
            or self._raw_field(raw, "type")
            or getattr(item, "title", None)
            or "tool"
        )
        return {
            "call_id": call_id,
            "tool_name": tool_name,
            "args": self._parse_json_object(self._raw_field(raw, "arguments")),
        }

    def _queue_event(self, se: ScanEvent) -> None:
        try:
            self._event_queue.put_nowait(se)
        except queue.Full:
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(se)
            except queue.Empty:
                pass

    def _emit_event(self, type: str, agent_id: str, content: str) -> None:
        se = ScanEvent(type=type, agent_id=agent_id, content=content, timestamp=time.time())
        try:
            self._event_queue.put_nowait(se)
        except queue.Full:
            pass

    def send_message(self, agent_id: str, text: str) -> bool:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return False

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
        self._scan_completed = True
        self._phase = "stopped"
        self._scan_status = "stopped"
        return not cancel_failed

    def poll_events(self) -> list[ScanEvent]:
        events: list[ScanEvent] = []
        while not self._event_queue.empty():
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        self._update_status_from_events(events)
        return events

    def _update_status_from_events(self, events: list[ScanEvent]) -> None:
        for ev in events:
            if ev.awaiting_input:
                self._awaiting_input = True
                self._input_prompt = ev.prompt or ""
            if ev.type == "scan_complete":
                self._phase = "completed"
                self._scan_completed = True
            elif ev.type == "scan_cancelled":
                self._phase = "stopped"
                self._scan_completed = True
            elif ev.type == "scan_error":
                self._phase = "failed"
                self._scan_completed = True
                self._last_error = ev.content

    def get_agent_tree(self) -> Optional[dict]:
        if not self._coordinator or not self._loop or self._loop.is_closed():
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._coordinator.graph_snapshot(),
                self._loop,
            )
            parent_of, statuses, names = future.result(timeout=10)
            tree: dict[str, Any] = {"agents": {}}
            for aid, parent in parent_of.items():
                tree["agents"][aid] = {
                    "id": aid,
                    "name": names.get(aid, aid),
                    "status": str(statuses.get(aid, "unknown")),
                    "parent_id": parent,
                }
            return tree
        except Exception as exc:
            logger.warning("get_agent_tree failed: %s", exc)
            return None

    def list_agents(self) -> list[dict]:
        """Return flat list of agents from coordinator."""
        tree = self.get_agent_tree()
        if not tree:
            return []
        return list(tree["agents"].values())

    def _discover_root_agent(self) -> None:
        """Discover root agent from coordinator (parent=None) and set _root_agent_id."""
        if not self._coordinator:
            return
        try:
            parent_of = getattr(self._coordinator, "parent_of", None)
            if parent_of is None:
                return
            for aid, parent in parent_of.items():
                if parent is None:
                    self._root_agent_id = aid
                    logger.debug("Root agent discovered: %s", aid)
                    return
        except Exception as exc:
            logger.debug("Root agent discovery failed: %s", exc)

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
        if self._thread:
            self._thread.join(timeout=5)
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def to_status_dict(self) -> dict[str, Any]:
        """Build a flat status dict compatible with job_status_text().

        Does NOT drain the event queue. Reads from cached state that is
        updated by poll_events(). Callers MUST call poll_events() first
        in their main loop to keep the cache current.
        """
        status = self.get_run_status()

        phase = self._phase
        if self._scan_status == "initializing":
            phase = "initializing"
        elif not status.get("is_running") and not status.get("is_active"):
            if not self._last_error and self._phase == "running":
                phase = "completed"

        state: dict[str, Any] = {
            "run_name": status.get("run_name", "pending"),
            "target": [],
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


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
