from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.models import JobPhase, StrixEvent


_PHASE_KEYWORDS: dict[str, JobPhase] = {
    "run.started": JobPhase.CONFIGURING,
    "run.configured": JobPhase.SCANNING,
    "agent.created": JobPhase.SCANNING,
    "tool.execution.started": JobPhase.SCANNING,
    "browser": JobPhase.BROWSER,
    "proxy": JobPhase.PROXY,
    "caido": JobPhase.PROXY,
    "analyzing": JobPhase.ANALYZING,
    "report": JobPhase.REPORTING,
    "report.generated": JobPhase.REPORTING,
    "run.completed": JobPhase.COMPLETED,
    "run.failed": JobPhase.FAILED,
    "run.stopped": JobPhase.STOPPED,
}


class EventStreamReader:
    def __init__(self, run_name: str, poll_interval: float = 1.0) -> None:
        self.run_name = run_name
        self.poll_interval = poll_interval
        self._event_path: Optional[Path] = None
        self._last_position: int = 0
        self._on_event: Optional[Callable[[StrixEvent], None]] = None
        self._on_phase_change: Optional[Callable[[JobPhase], None]] = None
        self._last_phase: Optional[JobPhase] = None

    def set_event_callback(self, func: Callable[[StrixEvent], None]) -> None:
        self._on_event = func

    def set_phase_callback(self, func: Callable[[JobPhase], None]) -> None:
        self._on_phase_change = func

    def _resolve_path(self) -> Optional[Path]:
        for candidate in [
            settings.strix_runs_dir / self.run_name / "events.jsonl",
            Path.cwd() / "strix_runs" / self.run_name / "events.jsonl",
        ]:
            if candidate.exists():
                return candidate
        return None

    def poll(self) -> list[StrixEvent]:
        if self._event_path is None:
            self._event_path = self._resolve_path()
            if self._event_path is None:
                return []
            self._last_position = self._event_path.stat().st_size

        current_size = self._event_path.stat().st_size
        if current_size <= self._last_position:
            return []

        events: list[StrixEvent] = []
        with open(self._event_path, "r") as f:
            f.seek(self._last_position)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ev = self._parse(raw)
                self._handle_phase(ev)
                if self._on_event:
                    self._on_event(ev)
                events.append(ev)

            self._last_position = f.tell()

        return events

    def _parse(self, raw: dict) -> StrixEvent:
        event_type = raw.get("event_type", raw.get("type", "unknown"))
        timestamp = raw.get("timestamp", "")
        data = {k: v for k, v in raw.items() if k not in ("event_type", "type", "timestamp", "run_name")}

        phase = self._classify(event_type, data)
        msg = data.get("message", data.get("status", data.get("description", "")))

        return StrixEvent(
            event_type=event_type,
            run_name=self.run_name,
            timestamp=timestamp,
            data=data,
            phase=phase,
            message=msg if isinstance(msg, str) else str(msg),
        )

    def _classify(self, event_type: str, data: dict) -> Optional[JobPhase]:
        for keyword, phase in _PHASE_KEYWORDS.items():
            if keyword in event_type.lower():
                return phase
        tool_name = data.get("tool", "").lower()
        for keyword, phase in _PHASE_KEYWORDS.items():
            if keyword in tool_name:
                return phase
        return None

    def _handle_phase(self, ev: StrixEvent) -> None:
        if ev.phase and ev.phase != self._last_phase:
            self._last_phase = ev.phase
            if self._on_phase_change:
                self._on_phase_change(ev.phase)

    def wait_for_phase(
        self, target_phase: JobPhase, timeout: float = 300
    ) -> Optional[JobPhase]:
        start = time.time()
        while time.time() - start < timeout:
            self.poll()
            if self._last_phase == target_phase:
                return target_phase
            time.sleep(self.poll_interval)
        return None

    def close(self) -> None:
        self._on_event = None
        self._on_phase_change = None
