"""pytest config — sets up environment for tests."""

from __future__ import annotations

import os

# Set dummy env vars before any module imports
os.environ.setdefault("STRIX_TG_TOKEN", "test:fake-token-for-testing-only")
os.environ.setdefault("STRIX_TG_ALLOWED_USERS", "12345")
os.environ.setdefault("STRIX_TG_ALLOWED_CHATS", "12345")
os.environ.setdefault("STRIX_BOT_DIR", os.getcwd())

import pytest


@pytest.fixture(autouse=True)
def _isolate_delivery_store(tmp_path, monkeypatch):
    """Point the per-run delivery tracker at a fresh tmp dir for every test.

    Prevents persisted delivery state (DELIVERED / TRANSIENT_FAILURE / ...) from
    leaking between tests via the shared workspace .bot-delivery dir.
    """
    from strix_telegram_bot.strix import delivery_state

    monkeypatch.setattr(
        delivery_state,
        "_default_store_dir",
        lambda: tmp_path / ".bot-delivery",
    )
