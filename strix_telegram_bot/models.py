from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScanMode(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ProfileType(str, Enum):
    INTERACTIVE = "interactive"
    HEADLESS = "headless"


class ScopeMode(str, Enum):
    AUTO = "auto"
    DIFF = "diff"
    FULL = "full"


class JobPhase(str, Enum):
    CREATED = "created"
    CONFIGURING = "configuring"
    SCANNING = "scanning"
    BROWSER = "browser"
    PROXY = "proxy"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class BridgePhase(str, Enum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class MenuState(str, Enum):
    MAIN = "main"
    WAITING_FOR_TARGETS = "waiting_for_targets"
    AGENT_SELECT = "agent_select"


@dataclass
class JobState:
    run_name: str
    target: list[str]
    mode: ScanMode = ScanMode.DEEP
    phase: JobPhase = JobPhase.CREATED
    instruction: str = ""
    start_time: float = field(default_factory=time.time)
    duration_sec: float = 0.0
    pid: Optional[int] = None
    error: Optional[str] = None
    awaiting_input: bool = False
    input_prompt: Optional[str] = None

    @property
    def elapsed(self) -> str:
        return _fmt_duration(time.time() - self.start_time)

    @property
    def is_active(self) -> bool:
        return self.phase in {
            JobPhase.CONFIGURING,
            JobPhase.SCANNING,
            JobPhase.BROWSER,
            JobPhase.PROXY,
            JobPhase.ANALYZING,
            JobPhase.REPORTING,
        }

    @property
    def is_terminal(self) -> bool:
        return self.phase in {
            JobPhase.COMPLETED,
            JobPhase.FAILED,
            JobPhase.STOPPED,
        }

    def to_dict(self) -> dict:
        return {
            "run_name": self.run_name,
            "target": self.target,
            "mode": self.mode.value,
            "phase": self.phase.value,
            "instruction": self.instruction,
            "start_time": self.start_time,
            "duration_sec": self.duration_sec,
            "pid": self.pid,
            "error": self.error,
            "awaiting_input": self.awaiting_input,
            "input_prompt": self.input_prompt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JobState":
        return cls(
            run_name=d["run_name"],
            target=d.get("target", []),
            mode=ScanMode(d.get("mode", "deep")),
            phase=JobPhase(d.get("phase", "created")),
            instruction=d.get("instruction", ""),
            start_time=d.get("start_time", time.time()),
            duration_sec=d.get("duration_sec", 0.0),
            pid=d.get("pid"),
            error=d.get("error"),
            awaiting_input=d.get("awaiting_input", False),
            input_prompt=d.get("input_prompt"),
        )


def _fmt_duration(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
