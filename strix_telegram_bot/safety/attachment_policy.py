from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

_ALLOWED_MIME_PREFIXES = [
    "text/",
    "application/json",
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-gtar",
    "image/png",
    "image/jpeg",
    "image/gif",
    "application/octet-stream",
]

_ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml",
    ".zip", ".tar", ".gz", ".tgz", ".7z",
    ".pdf", ".png", ".jpg", ".jpeg", ".gif",
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".kt",
    ".env", ".env.example",
    ".pem", ".key", ".crt",
    ".log",
}


def validate_attachment(
    file_name: str,
    file_size: int,
    mime_type: Optional[str] = None,
) -> tuple[bool, str]:
    if file_size > _MAX_SIZE_BYTES:
        return (
            False,
            f"File too large ({file_size / 1024 / 1024:.1f} MB). Max: {_MAX_SIZE_BYTES / 1024 / 1024:.0f} MB",
        )

    ext = Path(file_name).suffix.lower()
    if ext and ext not in _ALLOWED_EXTENSIONS:
        return (
            False,
            f"Extension '{ext}' not allowed. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS)[:10])}...",
        )

    if mime_type and not any(
        mime_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES
    ):
        return (
            False,
            f"MIME type '{mime_type}' not allowed",
        )

    return True, "OK"


def sanitize_target(target: str) -> tuple[bool, str]:
    import re

    if not target or len(target) > 4096:
        return False, "Target too long or empty"
    if re.search(r"[;\x00-\x1f]", target):
        return False, "Target contains invalid characters"
    if target.startswith("file://") or target.startswith("localhost"):
        return False, "Local targets not allowed"
    return True, "OK"
