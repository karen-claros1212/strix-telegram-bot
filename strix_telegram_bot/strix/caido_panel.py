from __future__ import annotations

import re
from typing import Optional


class CaidoPanel:
    CAIDO_URL_PATTERNS = [
        re.compile(r"https?://[a-z0-9.-]+:\d+"),
        re.compile(r"Caido.*?(?:at|on|url):?\s*(https?://\S+)", re.I),
        re.compile(r"(https?://caido[-.][a-z0-9.-]+(?::\d+)?)", re.I),
        re.compile(r"Proxy.*?(?:at|url):\s*(https?://\S+)", re.I),
    ]

    def __init__(self) -> None:
        self._url: Optional[str] = None
        self._active: bool = False

    @property
    def url(self) -> Optional[str]:
        return self._url

    @property
    def active(self) -> bool:
        return self._active

    def update_from_text(self, text: str) -> Optional[str]:
        for pattern in self.CAIDO_URL_PATTERNS:
            m = pattern.search(text)
            if m:
                found = m.group(1) if m.lastindex else m.group(0)
                found = found.rstrip("/,.;:")
                self._url = found
                self._active = True
                return found
        return None

    def set_url(self, url: str) -> None:
        self._url = url
        self._active = bool(url)

    def set_active(self, active: bool) -> None:
        self._active = active

    def clear(self) -> None:
        self._url = None
        self._active = False

    def status_line(self) -> str:
        if self._active and self._url:
            return f"Active: {self._url}"
        elif self._active:
            return "Active (URL unknown)"
        else:
            return "Inactive"
