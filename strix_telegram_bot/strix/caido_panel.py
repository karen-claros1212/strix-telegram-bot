from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings


class CaidoPanel:
    CAIDO_URL_PATTERNS = [
        re.compile(r"https?://[a-z0-9.-]+:\d+"),
        re.compile(r"Caido.*?(?:at|on|url):?\s*(https?://\S+)", re.I),
        re.compile(r"(https?://caido[-.][a-z0-9.-]+(?::\d+)?)", re.I),
        re.compile(r"Proxy.*?(?:at|url):\s*(https?://\S+)", re.I),
    ]

    PORT_PATTERNS = [
        re.compile(r"Caido.*?(?:port|listen).*?(\d{4,5})", re.I),
        re.compile(r"(?:port|listen).*?(\d{4,5})", re.I),
    ]

    def __init__(self) -> None:
        self._url: Optional[str] = None
        self._port: Optional[int] = None
        self._active: bool = False

    @property
    def url(self) -> Optional[str]:
        return self._url

    @property
    def port(self) -> Optional[int]:
        return self._port

    @property
    def active(self) -> bool:
        return self._active

    def update_from_text(self, text: str) -> Optional[str]:
        for pattern in self.PORT_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    self._port = int(m.group(1))
                except ValueError:
                    pass
                break

        for pattern in self.CAIDO_URL_PATTERNS:
            m = pattern.search(text)
            if m:
                found = m.group(1) if m.lastindex else m.group(0)
                found = found.rstrip("/,.;:")
                self._url = found
                self._active = True
                return found

        if self._port and not self._url:
            self._url = f"http://127.0.0.1:{self._port}"
            self._active = True
            return self._url

        return None

    def set_url(self, url: str) -> None:
        self._url = url
        self._active = bool(url)

    def set_active(self, active: bool) -> None:
        self._active = active

    def clear(self) -> None:
        self._url = None
        self._port = None
        self._active = False

    def status_line(self) -> str:
        if self._active and self._url:
            return f"Active: {self._url}"
        elif self._active:
            port_str = f" port {self._port}" if self._port else ""
            return f"Active{port_str} (URL unknown)"
        else:
            return "Inactive"

    def detect_caido_from_logs(self, run_name: str) -> bool:
        for candidate in [
            settings.strix_runs_dir / run_name / "logs",
            Path.cwd() / "strix_runs" / run_name / "logs",
        ]:
            if not candidate.exists():
                continue
            for log_file in candidate.iterdir():
                if log_file.is_file():
                    try:
                        text = log_file.read_text(errors="replace")
                        if self.update_from_text(text):
                            return True
                    except OSError:
                        continue
        return False

    def detect_caido_from_events(self, run_name: str) -> bool:
        for candidate in [
            settings.strix_runs_dir / run_name / "events.jsonl",
            Path.cwd() / "strix_runs" / run_name / "events.jsonl",
        ]:
            if not candidate.exists():
                continue
            try:
                with open(candidate) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        text = json.dumps(data)
                        if self.update_from_text(text):
                            return True
            except OSError:
                continue
        return False

    def detect_caido_port(self) -> Optional[int]:
        return self._port

    def get_caido_status(self, run_name: str) -> dict:
        if not self._active:
            self.detect_caido_from_events(run_name)
        if not self._active:
            self.detect_caido_from_logs(run_name)

        return {
            "active": self._active,
            "url": self._url,
            "port": self._port,
        }

    def build_caido_panel(self, run_name: str) -> str:
        status = self.get_caido_status(run_name)

        if not status["active"]:
            return (
                "Caido Proxy\n"
                "Status: Inactive\n"
                "\n"
                "Start a scan to see Caido here."
            )

        lines = ["Caido Proxy"]
        lines.append(f"Status: Active")
        if status["url"]:
            lines.append(f"URL: {status['url']}")
        if status["port"]:
            lines.append(f"Port: {status['port']}")

        lines.append("")
        lines.append("Caido allows manual traffic inspection,")
        lines.append("request replay, and interactive testing")
        lines.append("alongside the automated scan.")

        return "\n".join(lines)

    def collect_caido_artifacts(self, run_name: str) -> list[dict]:
        artifacts = []
        for candidate in [
            settings.strix_runs_dir / run_name / "caido",
            Path.cwd() / "strix_runs" / run_name / "caido",
            settings.strix_runs_dir / run_name / "evidence" / "caido",
        ]:
            if not candidate.exists():
                continue
            for fpath in candidate.rglob("*"):
                if fpath.is_file():
                    artifacts.append({
                        "name": fpath.name,
                        "path": str(fpath),
                        "size": fpath.stat().st_size,
                    })
        return artifacts
