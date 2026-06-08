from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from strix_telegram_bot.models import ScanMode, ApprovalRequest


class ApprovalGate:
    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._on_resolve: Optional[Callable[[ApprovalRequest, bool], None]] = None

    def set_resolver(self, func: Callable[[ApprovalRequest, bool], None]) -> None:
        self._on_resolve = func

    def request_approval(
        self,
        job_run_name: str,
        target: list[str],
        mode: ScanMode,
        reason: str,
        chat_id: int,
        message_id: int,
    ) -> str:
        req = ApprovalRequest(
            job_run_name=job_run_name,
            target=target,
            mode=mode,
            reason=reason,
            chat_id=chat_id,
            message_id=message_id,
        )
        self._pending[job_run_name] = req
        return job_run_name

    def resolve(self, job_run_name: str, approved: bool) -> Optional[ApprovalRequest]:
        req = self._pending.pop(job_run_name, None)
        if req is None:
            return None
        req.resolved = True
        if self._on_resolve:
            self._on_resolve(req, approved)
        return req

    def get_pending(self, job_run_name: str) -> Optional[ApprovalRequest]:
        return self._pending.get(job_run_name)

    def list_pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())


_approval_gate = ApprovalGate()
get_gate = lambda: _approval_gate
