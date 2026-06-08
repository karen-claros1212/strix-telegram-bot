from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.models import ScanMode, JobPhase
from strix_telegram_bot.safety.scope_policy import validate_scope


class StrixCliAdapter:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._run_name: Optional[str] = None
        self._runner = settings.strix_bin

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc and self._proc.returncode is None else None

    @property
    def run_name(self) -> Optional[str]:
        return self._run_name

    def _detect_run_name(self, stderr_line: str) -> Optional[str]:
        for prefix in ("Run: ", "Run name: ", "strix_runs/"):
            if prefix in stderr_line:
                idx = stderr_line.find("strix_runs/")
                if idx != -1:
                    rest = stderr_line[idx + len("strix_runs/"):]
                    return rest.split("/")[0].split()[0].strip()
                idx = stderr_line.find(prefix)
                if idx != -1:
                    return stderr_line[idx + len(prefix):].split()[0].strip()
        return None

    def _stderr_reader(self, fd: int) -> None:
        import io
        reader = io.BufferedReader(io.FileIO(fd, closefd=True))
        try:
            for raw in reader:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not self._run_name:
                    rn = self._detect_run_name(line)
                    if rn:
                        self._run_name = rn
        finally:
            reader.close()

    def build_args(
        self,
        targets: list[str],
        mode: ScanMode = ScanMode.DEEP,
        instruction: str = "",
        instruction_file: Optional[Path] = None,
        scope_mode: str = "auto",
        diff_base: Optional[str] = None,
        non_interactive: bool = True,
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
        non_interactive: bool = True,
    ) -> tuple[bool, str]:
        ok, msg = validate_scope(targets)
        if not ok:
            return False, msg

        args = self.build_args(
            targets=targets,
            mode=mode,
            instruction=instruction,
            instruction_file=instruction_file,
            scope_mode=scope_mode,
            non_interactive=non_interactive,
        )

        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            self._run_name = None
            return True, f"Started STRIX (PID {self._proc.pid})"
        except FileNotFoundError:
            return False, "STRIX CLI not found. Is strix-agent installed?"
        except Exception as e:
            return False, f"Failed to start STRIX: {e}"

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
        if not self._proc:
            return "", ""
        out = ""
        err = ""
        if self._proc.stdout:
            out = self._proc.stdout.read()
        if self._proc.stderr:
            err = self._proc.stderr.read()
        return out, err

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
        if self._proc and self._proc.returncode is None:
            self.stop()
        self._proc = None
        self._run_name = None
