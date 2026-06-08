"""Test configuration and environment variable loading."""

from __future__ import annotations

import os
import pytest


def test_settings_loads_token():
    token = os.environ.get("STRIX_TG_TOKEN", "")
    assert token, "STRIX_TG_TOKEN must be set for integration tests"
    assert len(token) > 10


def test_settings_llm_defaults():
    model = os.environ.get("STRIX_LLM", "deepseek/deepseek-v4-pro")
    assert model


def test_resolve_workspace():
    from strix_telegram_bot.config import resolve_workspace
    ws = resolve_workspace()
    assert ws.exists()
    assert ws.is_dir()


def test_resolve_strix_bin():
    from strix_telegram_bot.config import resolve_strix_bin
    bin_path = resolve_strix_bin()
    assert bin_path  # should return "strix" or a full path
    assert isinstance(bin_path, str)
