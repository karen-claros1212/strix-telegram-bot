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


def load_env_file(path: str | None = None) -> None:
    """Load .env_bot file into os.environ if variables are not already set.

    Searches:
    1. Explicit path
    2. Same directory as this file
    3. Current working directory
    """
    if path is None:
        candidates = [
            os.path.join(os.path.dirname(__file__), ".env_bot"),
            os.path.join(os.getcwd(), ".env_bot"),
        ]
        for c in candidates:
            if os.path.exists(c):
                path = c
                break
    if not path or not os.path.exists(path):
        return

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if "export " in key:
                key = key.replace("export ", "", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            # Only set if not already present (env vars take priority)
            if key not in os.environ:
                os.environ[key] = val


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_users: set[int]
    allowed_chats: set[int]
    work_root: Path
    job_timeout_seconds: int
    max_concurrent_jobs: int


def load_settings() -> Settings:
    # Load from .env_bot file first (if not already in environment)
    load_env_file()

    token = os.getenv("STRIX_TG_TOKEN", "").strip()
    allowed_users = _parse_id_list(os.getenv("STRIX_TG_ALLOWED_USERS"))
    allowed_chats = _parse_id_list(os.getenv("STRIX_TG_ALLOWED_CHATS"))
    work_root = Path(os.getenv("STRIX_WORK_ROOT", "./strix_runs")).resolve()
    timeout_raw = os.getenv("STRIX_JOB_TIMEOUT_SECONDS", "7200")
    try:
        job_timeout_seconds = int(timeout_raw)
    except ValueError:
        job_timeout_seconds = 7200

    concurrent_raw = os.getenv("STRIX_MAX_CONCURRENT_JOBS", "3")
    try:
        max_concurrent_jobs = int(concurrent_raw)
    except ValueError:
        max_concurrent_jobs = 3

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
        max_concurrent_jobs=max_concurrent_jobs,
    )
