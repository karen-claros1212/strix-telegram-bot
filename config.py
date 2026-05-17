from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_id_list(value: str | None) -> set[int]:
    if not value:
        return set()
    ids: set[int] = set()
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        ids.add(int(item))
    return ids


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_users: set[int]
    allowed_chats: set[int]
    work_root: Path
    job_timeout_seconds: int


def load_settings() -> Settings:
    token = os.getenv("STRIX_TG_TOKEN", "").strip()
    allowed_users = _parse_id_list(os.getenv("STRIX_TG_ALLOWED_USERS"))
    allowed_chats = _parse_id_list(os.getenv("STRIX_TG_ALLOWED_CHATS"))
    work_root = Path(os.getenv("STRIX_WORK_ROOT", "./strix_runs")).resolve()
    timeout_raw = os.getenv("STRIX_JOB_TIMEOUT_SECONDS", "7200")
    try:
        job_timeout_seconds = int(timeout_raw)
    except ValueError:
        job_timeout_seconds = 7200

    if not token:
        raise ValueError("STRIX_TG_TOKEN requerido")
    if not allowed_users:
        raise ValueError("STRIX_TG_ALLOWED_USERS requerido")

    work_root.mkdir(parents=True, exist_ok=True)

    return Settings(
        token=token,
        allowed_users=allowed_users,
        allowed_chats=allowed_chats,
        work_root=work_root,
        job_timeout_seconds=job_timeout_seconds,
    )
