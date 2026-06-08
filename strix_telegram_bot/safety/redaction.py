import re
from typing import Optional

_PATTERNS: list[tuple[str, str, re.Pattern, str]] = [
    ("api_key", "API Key", re.compile(
        r'(?i)(api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*["\']?([a-z0-9_\-]{16,})["\']?'
    ), "group"),
    ("token", "Token", re.compile(
        r'(?i)(token|bearer|jwt|auth[_-]?token)\s*[=:]\s*["\']?([a-z0-9_\-\.]{8,})["\']?'
    ), "group"),
    ("tg_token", "Telegram Token", re.compile(
        r'\d{8,10}:[a-z0-9_\-]{35}'
    ), "replace"),
    ("sk_key", "OpenAI-style Key", re.compile(
        r'sk-[a-z0-9]{32,}'
    ), "replace"),
    ("private_key", "Private Key Block", re.compile(
        r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'
    ), "block"),
    ("password", "Password Assignment", re.compile(
        r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?([^\s"\'@]{4,})["\']?'
    ), "group"),
    ("conn_str", "Connection String", re.compile(
        r'(?i)(postgresql|mysql|mongodb|redis)://[^\s]+'
    ), "replace"),
]


def redact_text(text: str, placeholder: str = "***") -> str:
    for name, label, pattern, kind in _PATTERNS:
        if kind == "block":
            text = re.sub(
                r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----',
                f"-----BEGIN PRIVATE KEY-----\n{placeholder}\n-----END PRIVATE KEY-----",
                text,
                flags=re.DOTALL,
            )
        elif kind == "group":
            text = pattern.sub(lambda m: f"{m.group(1)}: {placeholder}", text)
        else:
            text = pattern.sub(placeholder, text)
    return text


def redact_json(data: dict, placeholder: str = "***") -> dict:
    sensitive_keys = {
        "token", "api_key", "api_key", "secret", "password", "passwd",
        "authorization", "auth", "jwt", "access_token", "refresh_token",
        "private_key", "api_secret", "client_secret", "llm_api_key",
    }
    result = {}
    for k, v in data.items():
        if k.lower().replace("-", "_") in sensitive_keys:
            result[k] = placeholder
        elif isinstance(v, dict):
            result[k] = redact_json(v, placeholder)
        elif isinstance(v, str):
            result[k] = redact_text(v, placeholder)
        else:
            result[k] = v
    return result
