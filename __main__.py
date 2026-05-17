from __future__ import annotations

import logging
import re
import shutil
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .bot import build_app
from .config import load_settings


def setup_logging() -> None:
    logger = logging.getLogger("strix_bot")
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG)

    handler = RotatingFileHandler(
        "bot.log",
        maxBytes=10_485_760,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)

    for lib in ("telegram", "httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


log = logging.getLogger("strix_bot")


def cleanup_old_runs(work_root: Path, max_age_days: int = 7) -> int:
    cutoff = time.time() - (max_age_days * 86_400)
    removed = 0
    for entry in work_root.iterdir():
        if entry.is_dir() and len(entry.name) >= 8:
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                log.info("Cleaned up old run: %s (modified %s)",
                         entry.name,
                         datetime.fromtimestamp(mtime).isoformat())
                removed += 1
    return removed


def main() -> None:
    setup_logging()
    log.info("Strix Telegram Bot starting...")

    settings = load_settings()
    log.info("Settings: users=%s chats=%s timeout=%ds work_root=%s",
             settings.allowed_users, settings.allowed_chats,
             settings.job_timeout_seconds, settings.work_root)

    cleaned = cleanup_old_runs(settings.work_root)
    if cleaned:
        log.info("Cleanup removed %d old run(s)", cleaned)
    else:
        log.debug("No old runs to clean up")

    app = build_app(settings)
    log.info("Starting polling...")
    try:
        app.run_polling(close_loop=False)
    finally:
        log.info("Strix Telegram Bot stopped.")


if __name__ == "__main__":
    main()
