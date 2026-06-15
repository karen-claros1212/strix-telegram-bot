"""STRIX Control Center — entry point."""

from __future__ import annotations

import logging
import os
import sys

_VALID_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}


def _normalize_strix_environment() -> None:
    raw = os.getenv("STRIX_REASONING_EFFORT")

    if raw is None or not raw.strip():
        os.environ.pop("STRIX_REASONING_EFFORT", None)
        return

    value = raw.strip().lower()

    if value not in _VALID_REASONING_EFFORTS:
        raise RuntimeError(
            "STRIX_REASONING_EFFORT inválido. "
            "Valores permitidos: none, minimal, low, medium, high, xhigh."
        )

    os.environ["STRIX_REASONING_EFFORT"] = value


_normalize_strix_environment()

from .config import settings
from .bot import StrixBot


def _check() -> None:
    """Validate environment and exit."""
    errors: list[str] = []
    ok: list[str] = []

    if settings.tg_token:
        ok.append(f"STRIX_TG_TOKEN: {'***' + settings.tg_token[-4:]}")
    else:
        errors.append("STRIX_TG_TOKEN not set")

    if settings.llm_api_key:
        ok.append(f"LLM_API_KEY: {'***' + settings.llm_api_key[-4:]}")
    else:
        ok.append("LLM_API_KEY: not set (optional for some modes)")

    import shutil
    strix_path = shutil.which(settings.strix_bin)
    if strix_path:
        ok.append(f"STRIX binary: {strix_path}")
    else:
        errors.append(f"STRIX binary not found: '{settings.strix_bin}' — set STRIX_BIN")

    ok.append(f"Python: {sys.version.split()[0]}")
    ok.append(f"Bot dir: {settings.bot_dir}")
    ok.append(f"Runs dir: {settings.strix_runs_dir}")
    ok.append(f"Allowed users: {len(settings.allowed_users)} configured")
    ok.append(f"Allowed chats: {len(settings.allowed_chats)} configured")

    print("STRIX Control Center — Environment Check")
    print()
    for line in ok:
        print(f"  OK  {line}")
    if errors:
        print()
        for line in errors:
            print(f"  FAIL  {line}")
        sys.exit(1)
    else:
        print()
        print("All checks passed.")


def main() -> None:
    if "--check" in sys.argv:
        _check()
        return

    log_dir = settings.bot_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "strix_bot.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    logger = logging.getLogger("strix_bot.main")
    logger.info("STRIX Control Center v2.0.0 starting...")
    logger.info(f"Python: {sys.version}")
    logger.info(f"STRIX binary: {settings.strix_bin}")
    logger.info(f"Bot dir: {settings.bot_dir}")
    logger.info(f"Allowed users: {settings.allowed_users}")
    logger.info(f"Allowed chats: {settings.allowed_chats}")
    logger.info(f"LLM: {settings.llm_model}")

    bot = StrixBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        bot.shutdown()
        logger.info("STRIX Control Center stopped.")


if __name__ == "__main__":
    main()
