"""Copyright 2026 Diego Claros

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
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


def cleanup_stale_containers() -> int:
    """Remove any orphaned strix-scan-* containers from previous bot runs."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=strix-scan-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=15,
        )
        containers = [c.strip() for c in result.stdout.splitlines() if c.strip()]
        if not containers:
            log.debug("No stale strix containers found")
            return 0

        removed = 0
        for name in containers:
            proc = subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                log.info("🧹 Removed stale container: %s", name)
                removed += 1
            else:
                log.warning("Failed to remove stale container %s: %s", name, proc.stderr.strip())
        return removed
    except subprocess.TimeoutExpired:
        log.warning("Timeout listing stale containers")
        return 0
    except FileNotFoundError:
        log.debug("Docker not available, skipping container cleanup")
        return 0
    except Exception as e:
        log.warning("Error cleaning stale containers: %s", e)
        return 0


def cleanup_orphaned_runs(work_root: Path, max_age_hours: int = 2) -> int:
    """Remove run directories whose containers are already gone (orphaned)."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=strix-scan-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=15,
        )
        active_containers = set(c.strip() for c in result.stdout.splitlines() if c.strip())
    except Exception:
        active_containers = set()

    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for entry in work_root.iterdir():
        if not entry.is_dir() or len(entry.name) < 8:
            continue
        job_id = entry.name
        container_name = f"strix-scan-{job_id}"
        if container_name in active_containers:
            continue
        mtime = entry.stat().st_mtime
        if mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            log.info("Removed orphaned run: %s (no container, age > %dh)", job_id, max_age_hours)
            removed += 1
    return removed


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
    log.info("Settings: users=%s chats=%s timeout=%ds work_root=%s max_jobs=%d",
             settings.allowed_users, settings.allowed_chats,
             settings.job_timeout_seconds, settings.work_root,
             settings.max_concurrent_jobs)

    # Startup cleanup: stale containers + orphaned runs
    stale_containers = cleanup_stale_containers()
    if stale_containers:
        log.info("Startup: removed %d stale container(s)", stale_containers)

    orphaned = cleanup_orphaned_runs(settings.work_root)
    if orphaned:
        log.info("Startup: removed %d orphaned run(s)", orphaned)

    cleaned = cleanup_old_runs(settings.work_root)
    if cleaned:
        log.info("Startup: removed %d old run(s)", cleaned)
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
