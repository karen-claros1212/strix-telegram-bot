from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional


class ScanMode(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class TargetType(str, Enum):
    URL = "url"
    GITHUB = "github"
    LOCAL = "local"
    ATTACHMENT = "attachment"
    MULTI = "multi"


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


class ChatMode(str, Enum):
    FREE = "free"
    JOB_RESPONSE = "job_response"


class MenuState(str, Enum):
    MAIN = "main"
    NEW_PENTEST_TARGET = "new_pentest_target"
    NEW_PENTEST_DEPTH = "new_pentest_depth"
    NEW_PENTEST_SCOPE = "new_pentest_scope"
    JOB_DETAIL = "job_detail"
    REPORTS_LIST = "reports_list"
    REPORT_DETAIL = "report_detail"
    CAIDO = "caido"
    HEALTH = "health"
    CONFIG = "config"
    APPROVAL = "approval"


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
    caido_url: Optional[str] = None
    error: Optional[str] = None
    awaiting_input: bool = False
    input_prompt: Optional[str] = None
    chat_history: list[dict] = field(default_factory=list)

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
            "caido_url": self.caido_url,
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
            caido_url=d.get("caido_url"),
            error=d.get("error"),
            awaiting_input=d.get("awaiting_input", False),
            input_prompt=d.get("input_prompt"),
        )


@dataclass
class StrixEvent:
    event_type: str
    run_name: str
    timestamp: str
    data: dict = field(default_factory=dict)
    phase: Optional[JobPhase] = None
    message: str = ""


@dataclass
class ApprovalRequest:
    job_run_name: str
    target: list[str]
    mode: ScanMode
    reason: str
    chat_id: int
    message_id: int
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


def _fmt_duration(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
