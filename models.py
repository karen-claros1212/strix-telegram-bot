from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class JobContext:
    user_id: int
    chat_id: int
    message_id: int
    text: str
    attachments: list[Path]


@dataclass
class JobState:
    job_id: str
    work_dir: Path
    instruction_path: Path
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    last_output: str = ""
    pid: int | None = None
