from __future__ import annotations

import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.models import ScanMode


class StrixCliAdapter:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._run_name: Optional[str] = None
        self._runner = settings.strix_bin
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._stderr_queue: queue.Queue[str] = queue.Queue()
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_readers = threading.Event()

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc and self._proc.returncode is None else None

    @property
    def run_name(self) -> Optional[str]:
        return self._run_name

    def _detect_run_name(self, line: str) -> Optional[str]:
        for prefix in ("Run: ", "Run name: ", "strix_runs/"):
            if prefix in line:
                idx = line.find("strix_runs/")
                if idx != -1:
                    rest = line[idx + len("strix_runs/"):]
                    return rest.split("/")[0].split()[0].strip()
                idx = line.find(prefix)
                if idx != -1:
                    return line[idx + len(prefix):].split()[0].strip()
        return None

    def _pipe_reader(self, fd, enqueue: queue.Queue[str]) -> None:
        try:
            for raw in fd:
                if self._stop_readers.is_set():
                    break
                line = raw.decode("utf-8", errors="replace").rstrip() if isinstance(raw, bytes) else raw.rstrip()
                if not self._run_name:
                    rn = self._detect_run_name(line)
                    if rn:
                        self._run_name = rn
                enqueue.put_nowait(line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                fd.close()
            except OSError:
                pass

    def build_args(
        self,
        targets: list[str],
        mode: ScanMode = ScanMode.DEEP,
        instruction: str = "",
        instruction_file: Optional[Path] = None,
        scope_mode: str = "auto",
        diff_base: Optional[str] = None,
        non_interactive: bool = False,
    ) -> list[str]:
        args = [self._runner]
        for t in targets:
            args.extend(["--target", t])
        if mode != ScanMode.DEEP:
            args.extend(["--scan-mode", mode.value])
        if instruction:
            args.extend(["--instruction", instruction])
        if instruction_file:
            args.extend(["--instruction-file", str(instruction_file)])
        if scope_mode:
            args.extend(["--scope-mode", scope_mode])
        if diff_base:
            args.extend(["--diff-base", diff_base])
        if non_interactive:
            args.append("--non-interactive")
        return args

    def start(
        self,
        targets: list[str],
        mode: ScanMode = ScanMode.DEEP,
        instruction: str = "",
        instruction_file: Optional[Path] = None,
        scope_mode: str = "auto",
        non_interactive: bool = False,
    ) -> tuple[bool, str]:
        args = self.build_args(
            targets=targets,
            mode=mode,
            instruction=instruction,
            instruction_file=instruction_file,
            scope_mode=scope_mode,
            non_interactive=non_interactive,
        )

        try:
            self._stop_readers.clear()
            self._stdout_queue = queue.Queue()
            self._stderr_queue = queue.Queue()

            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )

            self._stdout_thread = threading.Thread(
                target=self._pipe_reader,
                args=(self._proc.stdout, self._stdout_queue),
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._pipe_reader,
                args=(self._proc.stderr, self._stderr_queue),
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

            self._run_name = None
            return True, f"Started STRIX (PID {self._proc.pid})"
        except FileNotFoundError:
            return False, "STRIX CLI not found. Is strix-agent installed?"
        except Exception as e:
            return False, f"Failed to start STRIX: {e}"

    def send_input(self, text: str) -> bool:
        if not self._proc or self._proc.returncode is not None or not self._proc.stdin:
            return False
        try:
            self._proc.stdin.write(text + "\n")
            self._proc.stdin.flush()
            return True
        except OSError:
            return False

    def stop(self, timeout: int = 15) -> bool:
        if not self._proc or self._proc.returncode is not None:
            return False
        try:
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            return True
        except Exception:
            return False

    def poll_output(self) -> tuple[str, str]:
        out_lines: list[str] = []
        err_lines: list[str] = []
        while not self._stdout_queue.empty():
            try:
                out_lines.append(self._stdout_queue.get_nowait())
            except queue.Empty:
                break
        while not self._stderr_queue.empty():
            try:
                err_lines.append(self._stderr_queue.get_nowait())
            except queue.Empty:
                break
        return "\n".join(out_lines), "\n".join(err_lines)

    def poll_returncode(self) -> Optional[int]:
        if not self._proc:
            return None
        self._proc.poll()
        return self._proc.returncode

    def is_running(self) -> bool:
        if not self._proc:
            return False
        self._proc.poll()
        return self._proc.returncode is None

    def cleanup(self) -> None:
        self._stop_readers.set()
        if self._proc and self._proc.returncode is None:
            self.stop()
        if self._stdout_thread:
            self._stdout_thread.join(timeout=3)
        if self._stderr_thread:
            self._stderr_thread.join(timeout=3)
        self._proc = None
        self._run_name = None
        self._stdout_queue = queue.Queue()
        self._stderr_queue = queue.Queue()
