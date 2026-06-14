"""StrixRuntimeBridge — asyncio thread wrapping AgentCoordinator + run_strix_scan."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from strix_telegram_bot.config import settings

_STRIX_AVAILABLE = False
AgentCoordinator: Any = None
run_strix_scan: Any = None
ReportState: Any = None
set_global_report_state: Any = None

try:
    from strix.core.agents import AgentCoordinator as _AC
    from strix.core.runner import run_strix_scan as _rss
    from strix.report.state import ReportState as _RS, set_global_report_state as _sgrs

    AgentCoordinator = _AC
    run_strix_scan = _rss
    ReportState = _RS
    set_global_report_state = _sgrs
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
        self._start_time: float = 0.0

    @property
    def is_available(self) -> bool:
        return _STRIX_AVAILABLE

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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
        agent_name: str = "strix",
    ) -> tuple[bool, str]:
        if not _STRIX_AVAILABLE:
            return False, "STRIX 1.0.4 no está instalado (strix package not found)"
        if self.is_running:
            return False, "Ya hay un escaneo en ejecución"

        targets_info = self._build_targets_info(targets)
        scan_config: dict[str, Any] = {
            "targets_info": targets_info,
            "instruction": instruction if instruction else None,
            "scan_mode": scan_mode,
            "scope_mode": scope_mode,
            "agent_name": agent_name,
        }
        if diff_base:
            scan_config["diff_base"] = diff_base
        if non_interactive:
            scan_config["non_interactive"] = True

        self._stop_event.clear()
        self._coordinator = AgentCoordinator()
        self._root_agent_id = None
        self._run_name = None
        self._start_time = time.time()

        self._thread = threading.Thread(
            target=self._scan_thread,
            args=(scan_config,),
            daemon=True,
        )
        self._thread.start()

        for _ in range(100):
            if self._run_name:
                break
            time.sleep(0.1)

        if self._run_name:
            return True, f"Escaneo iniciado: {self._run_name}"
        return True, "Escaneo iniciado..."

    @staticmethod
    def _build_targets_info(targets: list[str]) -> list[dict]:
        info: list[dict] = []
        for t in targets:
            t = t.strip()
            if not t:
                continue
            if t.startswith(("http://", "https://")):
                info.append({"type": "url", "value": t})
            elif t.startswith("git@") or t.endswith(".git"):
                info.append({"type": "git", "value": t})
            elif t.startswith(("/", "~", ".")):
                info.append({"type": "local", "value": t})
            else:
                info.append({"type": "url", "value": f"https://{t}"})
        return info

    def _scan_thread(self, scan_config: dict) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            rs = ReportState()
            set_global_report_state(rs)

            result = self._loop.run_until_complete(
                run_strix_scan(
                    scan_config=scan_config,
                    coordinator=self._coordinator,
                    interactive=True,
                    event_sink=self._capture_event,
                )
            )
            self._emit_event("scan_complete", "", "Escaneo finalizado")
        except asyncio.CancelledError:
            self._emit_event("scan_cancelled", "", "Escaneo cancelado")
        except Exception as e:
            self._emit_event("scan_error", "", f"Error en escaneo: {e}")
        finally:
            self._loop.close()
            self._loop = None

    def _capture_event(self, agent_id: str, event: dict) -> None:
        ev_type = event.get("type", "unknown") if isinstance(event, dict) else "unknown"
        content = ""
        prompt = ""
        awaiting = False

        if isinstance(event, dict):
            content = event.get("content", event.get("message", ""))
            prompt = event.get("prompt", "")
            awaiting = event.get("type") == "input_request"

            if ev_type == "agent.created" and not self._root_agent_id:
                self._root_agent_id = agent_id if agent_id else event.get("agent_id", "")

            run_n = event.get("run_name") or event.get("scan_id", "")
            if run_n and not self._run_name:
                self._run_name = run_n
        else:
            content = str(event)

        timestamp = event.get("timestamp", time.time()) if isinstance(event, dict) else time.time()

        se = ScanEvent(
            type=ev_type,
            agent_id=agent_id,
            content=str(content),
            timestamp=timestamp,
            awaiting_input=awaiting,
            prompt=str(prompt),
        )
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
            asyncio.run_coroutine_threadsafe(
                self._coordinator.send(agent_id, message),
                self._loop,
            )
            return True
        except Exception:
            return False

    def send_message_to_agent(self, text: str, agent_id: Optional[str] = None) -> bool:
        aid = agent_id or self._root_agent_id or "strix"
        return self.send_message(aid, text)

    def stop_scan(self) -> bool:
        self._stop_event.set()
        if self._coordinator and self._loop and not self._loop.is_closed():
            try:
                root = self._root_agent_id or "strix"
                asyncio.run_coroutine_threadsafe(
                    self._coordinator.stop_agent(root),
                    self._loop,
                )
                return True
            except Exception:
                pass
        return False

    def poll_events(self) -> list[ScanEvent]:
        events: list[ScanEvent] = []
        while not self._event_queue.empty():
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def get_agent_tree(self) -> Optional[dict]:
        if not self._coordinator:
            return None
        try:
            return self._coordinator.graph_snapshot()
        except Exception:
            return None

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
        if self._coordinator and self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._coordinator.stop_agent(self._root_agent_id or "strix"),
                    self._loop,
                )
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def to_status_dict(self) -> dict[str, Any]:
        """Build a flat status dict compatible with job_status_text()."""
        status = self.get_run_status()
        events = self.poll_events()

        state: dict[str, Any] = {
            "run_name": status.get("run_name", "pending"),
            "target": [],
            "mode": status.get("mode", "deep"),
            "phase": status.get("phase", "running"),
            "elapsed": _fmt_duration(status["elapsed"]),
            "error": status.get("error"),
            "is_active": status["is_running"],
            "awaiting_input": False,
            "input_prompt": None,
        }

        for ev in events:
            if ev.type == "scan_complete":
                state["phase"] = "completed"
                state["is_active"] = False
            elif ev.type == "scan_cancelled":
                state["phase"] = "stopped"
                state["is_active"] = False
            elif ev.type == "scan_error":
                state["phase"] = "failed"
                state["is_active"] = False
                state["error"] = ev.content
            if ev.awaiting_input:
                state["awaiting_input"] = True
                state["input_prompt"] = ev.prompt

        if not status["is_running"]:
            state["is_active"] = False
            if not state.get("error") and state["phase"] not in ("completed", "stopped", "failed"):
                state["phase"] = "completed"

        return state


def _fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
