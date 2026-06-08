from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from strix_telegram_bot.config import settings


_STATE_FILE = "bot_state.json"


class StateManager:
    def __init__(self, state_dir: Optional[Path] = None) -> None:
        self._dir = state_dir or (settings.strix_runs_dir / ".bot-state")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load()

    def _path(self) -> Path:
        return self._dir / _STATE_FILE

    def _load(self) -> dict:
        p = self._path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        try:
            self._path().write_text(json.dumps(
                self._state, indent=2, default=str,
            ))
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value
        self._save()

    def update(self, mapping: dict) -> None:
        self._state.update(mapping)
        self._save()

    def delete(self, key: str) -> bool:
        result = self._state.pop(key, None) is not None
        if result:
            self._save()
        return result

    def get_all(self) -> dict:
        return dict(self._state)

    def get_active_job_id(self) -> Optional[str]:
        return self._state.get("active_job_id")

    def set_active_job_id(self, job_id: str) -> None:
        self.set("active_job_id", job_id)

    def clear_active_job(self) -> None:
        self.delete("active_job_id")

    def get_event_checkpoint(self, run_name: str) -> int:
        cps = self._state.get("event_checkpoints", {})
        return cps.get(run_name, 0)

    def set_event_checkpoint(self, run_name: str, position: int) -> None:
        cps = self._state.setdefault("event_checkpoints", {})
        cps[run_name] = position
        self._save()

    def get_last_update_time(self) -> float:
        return self._state.get("last_update_time", 0.0)

    def set_last_update_time(self, t: float) -> None:
        self.set("last_update_time", t)

    def to_dict(self) -> dict:
        return {
            "active_job_id": self.get_active_job_id(),
            "event_checkpoints": self._state.get("event_checkpoints", {}),
            "last_update_time": self.get_last_update_time(),
            "uptime_start": self._state.get("uptime_start", time.time()),
        }


_state_manager = StateManager()
get_state_manager = lambda: _state_manager
