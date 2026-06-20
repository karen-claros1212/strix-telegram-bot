import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            line = line.removeprefix("export ").strip()
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'")
            if key and not os.environ.get(key):
                os.environ[key] = val


def _find_dotenv() -> Optional[Path]:
    candidates = [
        Path.cwd() / ".env_bot",
        Path.cwd() / ".env",
        Path.home() / ".strix" / ".env_bot",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


_dotenv = _find_dotenv()
if _dotenv:
    _load_dotenv(_dotenv)

# Normalize STRIX_REASONING_EFFORT immediately after loading .env
# so runtime_bridge (which imports strix.config) sees a valid value.
_VALID_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}


def _normalize_reasoning_effort() -> None:
    raw = os.getenv("STRIX_REASONING_EFFORT")

    if raw is None or not raw.strip():
        os.environ.pop("STRIX_REASONING_EFFORT", None)
        return

    value = raw.strip().lower()

    if value not in _VALID_REASONING_EFFORTS:
        raise RuntimeError(
            "STRIX_REASONING_EFFORT inválido: "
            "usa none, minimal, low, medium, high o xhigh."
        )

    os.environ["STRIX_REASONING_EFFORT"] = value


_normalize_reasoning_effort()


def resolve_workspace() -> Path:
    env = os.environ.get("STRIX_BOT_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd()


def resolve_strix_bin() -> str:
    env = os.environ.get("STRIX_BIN")
    if env:
        return env
    which = shutil.which("strix")
    if which:
        return which
    return "strix"


def resolve_strix_runs_dir() -> Path:
    env = os.environ.get("STRIX_RUNS_DIR")
    if env:
        return Path(env).resolve()
    return resolve_workspace() / "strix_runs"


WORKSPACE = resolve_workspace()


@dataclass(frozen=True)
class Settings:
    tg_token: str
    allowed_users: frozenset[str]
    allowed_chats: frozenset[str]
    llm_model: str
    llm_api_key: str
    strix_bin: str = field(default_factory=resolve_strix_bin)
    strix_runs_dir: Path = field(default_factory=resolve_strix_runs_dir)
    bot_dir: Path = field(default_factory=resolve_workspace)

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self.tg_token}"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("STRIX_TG_TOKEN", "")
        if not token:
            print("FATAL: STRIX_TG_TOKEN not set", file=sys.stderr)
            sys.exit(1)

        users_str = os.environ.get("STRIX_TG_ALLOWED_USERS", "")
        chats_str = os.environ.get("STRIX_TG_ALLOWED_CHATS", "")

        return cls(
            tg_token=token,
            allowed_users=frozenset(
                u.strip() for u in users_str.split(",") if u.strip()
            ),
            allowed_chats=frozenset(
                c.strip() for c in chats_str.split(",") if c.strip()
            ),
            llm_model=os.environ.get(
                "STRIX_LLM", "deepseek/deepseek-v4-pro"
            ),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
        )


class _SettingsProxy:
    _instance: Optional[Settings] = None

    def __getattr__(self, name: str):
        if self._instance is None:
            self._instance = Settings.from_env()
        return getattr(self._instance, name)


settings = _SettingsProxy()
