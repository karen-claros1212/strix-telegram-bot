from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from strix_telegram_bot.models import JobPhase, JobState, ScanMode
from strix_telegram_bot.strix.cli_adapter import StrixCliAdapter
from strix_telegram_bot.strix.event_reader import EventStreamReader
from strix_telegram_bot.strix.report_collector import ReportCollector
from strix_telegram_bot.strix.caido_panel import CaidoPanel
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.jobs.process_control import ProcessController


class JobRunner:
    def __init__(
        self,
        job_store: JobStore,
        process_controller: ProcessController,
    ) -> None:
        self._store = job_store
        self._controller = process_controller
        self._cli: Optional[StrixCliAdapter] = None
        self._reader: Optional[EventStreamReader] = None
        self._reporter: Optional[ReportCollector] = None
        self._caido = CaidoPanel()
        self._state: Optional[JobState] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_update: Optional[Callable[[JobState], None]] = None
        self._on_input_request: Optional[Callable[[str, str], None]] = None
        self._lock = threading.Lock()
        self.update_queue: queue.Queue[JobState] = queue.Queue()

    def set_update_callback(self, func: Callable[[JobState], None]) -> None:
        self._on_update = func

    def set_input_callback(self, func: Callable[[str, str], None]) -> None:
        self._on_input_request = func

    @property
    def state(self) -> Optional[JobState]:
        with self._lock:
            return self._state

    @property
    def caido(self) -> CaidoPanel:
        return self._caido

    def start(
        self,
        targets: list[str],
        mode: ScanMode = ScanMode.DEEP,
        instruction: str = "",
        instruction_file: Optional[Path] = None,
        scope_mode: str = "auto",
    ) -> tuple[bool, str]:
        if self._state and self._state.is_active:
            return False, "A job is already running"

        self._cli = StrixCliAdapter()
        ok, msg = self._cli.start(
            targets=targets,
            mode=mode,
            instruction=instruction,
            instruction_file=instruction_file,
            scope_mode=scope_mode,
        )
        if not ok:
            return False, msg

        self._stop_event.clear()
        self._caido.clear()

        with self._lock:
            self._state = JobState(
                run_name="pending",
                target=targets,
                mode=mode,
                phase=JobPhase.CREATED,
                instruction=instruction,
                start_time=time.time(),
            )
            if self._cli.pid:
                self._state.pid = self._cli.pid

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
        )
        self._thread.start()
        return True, msg

    def _run_loop(self) -> None:
        start = time.time()
        while not self._stop_event.is_set():
            if not self._cli:
                break

            returncode = self._cli.poll_returncode()
            if returncode is not None:
                with self._lock:
                    if self._state:
                        if returncode == 0:
                            self._state.phase = JobPhase.COMPLETED
                        elif returncode == -15 or returncode == -2:
                            self._state.phase = JobPhase.STOPPED
                        else:
                            self._state.phase = JobPhase.FAILED
                            self._state.error = f"Exit code {returncode}"
                        self._state.duration_sec = time.time() - start
                        self._store.save(self._state)
                self._emit_update()
                break

            time.sleep(1)

            if self._reader is None and self._cli and self._cli.run_name:
                rn = self._cli.run_name
                with self._lock:
                    if self._state:
                        self._state.run_name = rn
                        self._store.save(self._state)
                self._reader = EventStreamReader(rn)
                self._reporter = ReportCollector(rn)
                self._reader.set_phase_callback(self._on_phase)

            if self._reader:
                self._reader.poll()
                _, stderr = self._cli.poll_output()
                if stderr:
                    caido_url = self._caido.update_from_text(stderr)
                    if caido_url:
                        with self._lock:
                            if self._state:
                                self._state.caido_url = caido_url
                                self._store.save(self._state)
                        self._emit_update()

            with self._lock:
                if self._state:
                    self._state.duration_sec = time.time() - start

            self._emit_update()

        self._cleanup()

    def _on_phase(self, phase: JobPhase) -> None:
        with self._lock:
            if self._state:
                self._state.phase = phase
                self._store.save(self._state)
        self._emit_update()

    def _emit_update(self) -> None:
        with self._lock:
            if self._state:
                try:
                    self.update_queue.put_nowait(self._state)
                except queue.Full:
                    pass

    def stop(self) -> bool:
        self._stop_event.set()
        if self._cli:
            res = self._cli.stop()
            with self._lock:
                if self._state and self._state.is_active:
                    self._state.phase = JobPhase.STOPPED
                    self._store.save(self._state)
            self._emit_update()
            return res
        return False

    def inject_input(self, text: str) -> bool:
        with self._lock:
            if self._state:
                self._state.chat_history.append({
                    "role": "user",
                    "text": text,
                    "timestamp": time.time(),
                })
                self._state.awaiting_input = False
                self._state.input_prompt = None
                self._store.save(self._state)

        if self._cli:
            return self._cli.send_input(text)
        return False

    def cleanup(self) -> None:
        self._stop_event.set()
        if self._cli:
            self._cli.cleanup()
        if self._reader:
            self._reader.close()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            if self._state:
                self._state.duration_sec = time.time() - self._state.start_time
                self._store.save(self._state)
