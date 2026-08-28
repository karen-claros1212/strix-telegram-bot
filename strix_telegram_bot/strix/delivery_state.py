"""Per-run, persisted report-delivery state machine.

Each run owns an independent :class:`DeliveryRecord` (no cross-run fallback).
State survives restarts via one JSON file per run in the store dir.

States:
    NOT_ELIGIBLE       — initial; run not yet confirmed completed / no report
    PENDING            — run completed, report expected, awaiting (re)delivery
    DELIVERING         — actively sending the document
    DELIVERED          — success (terminal)
    TRANSIENT_FAILURE  — send hiccup; retry after cooldown
    PERMANENT_FAILURE  — client/file error; stop retrying (terminal)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings


def _default_store_dir() -> Path:
    """Default per-run delivery store dir (patchable for tests)."""
    return settings.strix_runs_dir / ".bot-delivery"


class DeliveryState(str, Enum):
    NOT_ELIGIBLE = "not_eligible"
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"

    @property
    def is_terminal(self) -> bool:
        return self in (DeliveryState.DELIVERED, DeliveryState.PERMANENT_FAILURE)

    @property
    def is_retryable(self) -> bool:
        return self in (DeliveryState.PENDING, DeliveryState.TRANSIENT_FAILURE)


@dataclass
class DeliveryRecord:
    run_name: str
    chat_id: int
    state: DeliveryState = DeliveryState.NOT_ELIGIBLE
    attempt_count: int = 0
    last_attempt: float = 0.0
    delivered: bool = False

    def to_dict(self) -> dict:
        return {
            "run_name": self.run_name,
            "chat_id": self.chat_id,
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "last_attempt": self.last_attempt,
            "delivered": self.delivered,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeliveryRecord":
        try:
            state = DeliveryState(data.get("state", "not_eligible"))
        except ValueError:
            state = DeliveryState.NOT_ELIGIBLE
        return cls(
            run_name=data["run_name"],
            chat_id=data.get("chat_id", 0),
            state=state,
            attempt_count=data.get("attempt_count", 0),
            last_attempt=data.get("last_attempt", 0.0),
            delivered=data.get("delivered", False),
        )


class ReportDeliveryTracker:
    """Persisted per-run delivery state machine (one record per run)."""

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        self._dir = store_dir or _default_store_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, DeliveryRecord] = {}
        self._load_all()

    def _path(self, run_name: str) -> Path:
        return self._dir / f"{run_name}.json"

    def _load_all(self) -> None:
        for fpath in self._dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text())
                rec = DeliveryRecord.from_dict(data)
                self._cache[rec.run_name] = rec
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                fpath.unlink(missing_ok=True)

    def get(self, run_name: str) -> Optional[DeliveryRecord]:
        return self._cache.get(run_name)

    def get_or_create(self, run_name: str, chat_id: int = 0) -> DeliveryRecord:
        rec = self._cache.get(run_name)
        if rec is None:
            rec = DeliveryRecord(
                run_name=run_name,
                chat_id=chat_id,
                state=DeliveryState.NOT_ELIGIBLE,
            )
            self._cache[run_name] = rec
        elif chat_id and rec.chat_id != chat_id:
            rec.chat_id = chat_id
        return rec

    def save(self, rec: DeliveryRecord) -> None:
        self._cache[rec.run_name] = rec
        self._path(rec.run_name).write_text(json.dumps(rec.to_dict(), indent=2, default=str))

    def set_state(self, run_name: str, state: DeliveryState, chat_id: int = 0) -> DeliveryRecord:
        rec = self.get_or_create(run_name, chat_id)
        rec.state = state
        if state == DeliveryState.DELIVERED:
            rec.delivered = True
        self.save(rec)
        return rec

    def record_attempt(self, run_name: str, outcome_kind: str, chat_id: int = 0) -> DeliveryRecord:
        """Record a delivery attempt outcome.

        outcome_kind: "success" | "transient" | "permanent"
        """
        rec = self.get_or_create(run_name, chat_id)
        rec.attempt_count += 1
        rec.last_attempt = time.time()
        if outcome_kind == "success":
            rec.state = DeliveryState.DELIVERED
            rec.delivered = True
        elif outcome_kind == "permanent":
            rec.state = DeliveryState.PERMANENT_FAILURE
        else:
            rec.state = DeliveryState.TRANSIENT_FAILURE
        self.save(rec)
        return rec

    def recover(self) -> int:
        """On restart: a run stuck in DELIVERING (crashed mid-send) becomes PENDING."""
        recovered = 0
        for rec in list(self._cache.values()):
            if rec.state == DeliveryState.DELIVERING:
                rec.state = DeliveryState.PENDING
                self.save(rec)
                recovered += 1
        return recovered

    def list_needing_delivery(self) -> list[DeliveryRecord]:
        return [
            r for r in self._cache.values()
            if r.state.is_retryable or r.state == DeliveryState.DELIVERING
        ]

    def list_all(self) -> list[DeliveryRecord]:
        return list(self._cache.values())

    def delete(self, run_name: str) -> bool:
        self._cache.pop(run_name, None)
        p = self._path(run_name)
        if p.exists():
            p.unlink()
            return True
        return False
