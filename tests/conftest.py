"""pytest config — sets up environment for tests."""

from __future__ import annotations

import os

# Set dummy env vars before any module imports
os.environ.setdefault("STRIX_TG_TOKEN", "test:fake-token-for-testing-only")
os.environ.setdefault("STRIX_TG_ALLOWED_USERS", "12345")
os.environ.setdefault("STRIX_TG_ALLOWED_CHATS", "12345")
os.environ.setdefault("STRIX_BOT_DIR", os.getcwd())
