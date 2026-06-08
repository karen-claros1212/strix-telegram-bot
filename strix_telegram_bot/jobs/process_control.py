from __future__ import annotations

import os
import signal
from typing import Optional


class ProcessController:
    def __init__(self) -> None:
        self._pids: dict[str, int] = {}

    def register(self, run_name: str, pid: int) -> None:
        self._pids[run_name] = pid

    def unregister(self, run_name: str) -> None:
        self._pids.pop(run_name, None)

    def get_pid(self, run_name: str) -> Optional[int]:
        return self._pids.get(run_name)

    def is_alive(self, run_name: str) -> bool:
        pid = self._pids.get(run_name)
        if pid is None:
            return False
        return self._pid_alive(pid)

    def stop(self, run_name: str, timeout: int = 15) -> bool:
        pid = self._pids.get(run_name)
        if pid is None:
            return False
        return self._stop_pid(pid, timeout)

    def stop_all(self, timeout: int = 15) -> int:
        stopped = 0
        for run_name in list(self._pids.keys()):
            if self.stop(run_name, timeout):
                stopped += 1
        return stopped

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _stop_pid(self, pid: int, timeout: int) -> bool:
        try:
            os.kill(pid, signal.SIGINT)
            return True
        except (OSError, ProcessLookupError):
            self._pids = {
                k: v for k, v in self._pids.items() if v != pid
            }
            return False
