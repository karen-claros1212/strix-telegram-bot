from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional


def classify_attachment(
    file_path: Path,
    original_name: str,
) -> dict:
    stat = file_path.stat()
    sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    ext = file_path.suffix.lower()
    size_mb = stat.st_size / (1024 * 1024)

    return {
        "original_name": original_name,
        "safe_name": _safe_name(original_name),
        "size_bytes": stat.st_size,
        "size_mb": round(size_mb, 1),
        "sha256": sha256,
        "extension": ext,
        "mime_type": _guess_mime(ext),
        "over_limit_telegram": size_mb > 50,
    }


def sanitize_target(target: str) -> tuple[bool, str]:
    if not target or len(target) > 4096:
        return False, "Target too long or empty"
    if re.search(r"[;\x00-\x1f]", target):
        return False, "Target contains invalid characters"
    return True, "OK"


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)


_MIME_MAP = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".xml": "application/xml",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".log": "text/plain",
    ".pem": "application/x-pem-file",
    ".key": "application/x-pem-file",
    ".crt": "application/x-x509-ca-cert",
    ".pcap": "application/vnd.tcpdump.pcap",
    ".har": "application/json",
    ".exe": "application/x-msdownload",
    ".dll": "application/x-msdownload",
    ".apk": "application/vnd.android.package-archive",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".bin": "application/octet-stream",
    ".dump": "application/octet-stream",
    ".dmp": "application/octet-stream",
    ".elf": "application/x-elf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}


def _guess_mime(ext: str) -> str:
    return _MIME_MAP.get(ext, "application/octet-stream")
