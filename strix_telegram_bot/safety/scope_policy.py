from __future__ import annotations

import re
from typing import Optional

_DENIED_DOMAINS: set[str] = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "10.", "172.16.", "192.168.",
    "169.254.",
}
_DENIED_SCHEMES = {"file://", "ftp://"}
_MAX_TARGETS = 5


def validate_scope(targets: list[str]) -> tuple[bool, str]:
    if len(targets) > _MAX_TARGETS:
        return (
            False,
            f"Max {_MAX_TARGETS} targets per run. Got {len(targets)}.",
        )

    bad: list[str] = []
    for t in targets:
        t_lower = t.lower().strip()
        for denied in _DENIED_SCHEMES:
            if t_lower.startswith(denied):
                bad.append(f"{t}: scheme not allowed")
                continue
        for denied in _DENIED_DOMAINS:
            if denied in t_lower:
                bad.append(f"{t}: internal/private target")
                break

    if bad:
        return False, "Scope violations:\n" + "\n".join(bad)
    return True, "OK"


def classify_target(target: str) -> str:
    t = target.strip()
    if "github.com" in t or t.startswith("git@") or t.endswith(".git"):
        return "repo"
    if t.startswith(("http://", "https://")):
        return "web"
    if t.startswith("/") or t.startswith("."):
        return "local"
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", t):
        return "ip"
    if "." in t and not t.startswith(("http", "git")):
        return "domain"
    return "unknown"
