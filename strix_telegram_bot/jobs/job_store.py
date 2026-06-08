from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.models import JobState


class JobStore:
    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self._dir = store_dir or (settings.strix_runs_dir / ".bot-jobs")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, JobState] = {}
        self._load_all()

    def _path(self, run_name: str) -> Path:
        return self._dir / f"{run_name}.json"

    def _load_all(self) -> None:
        for fpath in self._dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text())
                job = JobState.from_dict(data)
                self._cache[job.run_name] = job
            except (json.JSONDecodeError, KeyError):
                fpath.unlink(missing_ok=True)

    def save(self, job: JobState) -> None:
        self._cache[job.run_name] = job
        self._path(job.run_name).write_text(
            json.dumps(job.to_dict(), indent=2, default=str)
        )

    def get(self, run_name: str) -> Optional[JobState]:
        return self._cache.get(run_name)

    def delete(self, run_name: str) -> bool:
        self._cache.pop(run_name, None)
        p = self._path(run_name)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_active(self) -> list[JobState]:
        return [j for j in self._cache.values() if j.is_active]

    def list_recent(self, limit: int = 10) -> list[JobState]:
        sorted_jobs = sorted(
            self._cache.values(),
            key=lambda j: j.start_time,
            reverse=True,
        )
        return sorted_jobs[:limit]

    def list_all(self) -> list[JobState]:
        return list(self._cache.values())
