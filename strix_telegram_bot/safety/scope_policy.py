from __future__ import annotations

import re


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
