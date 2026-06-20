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


class TestReasoningEffortNormalization:
    """STRIX_REASONING_EFFORT normalization in config.py."""

    def test_empty_string_is_removed(self, monkeypatch):
        monkeypatch.setenv("STRIX_REASONING_EFFORT", "")
        from strix_telegram_bot.config import _normalize_reasoning_effort
        _normalize_reasoning_effort()
        assert "STRIX_REASONING_EFFORT" not in os.environ

    def test_absent_is_removed(self, monkeypatch):
        monkeypatch.delenv("STRIX_REASONING_EFFORT", raising=False)
        from strix_telegram_bot.config import _normalize_reasoning_effort
        _normalize_reasoning_effort()
        assert "STRIX_REASONING_EFFORT" not in os.environ

    def test_valid_value_is_normalized(self, monkeypatch):
        monkeypatch.setenv("STRIX_REASONING_EFFORT", " HIGH ")
        from strix_telegram_bot.config import _normalize_reasoning_effort
        _normalize_reasoning_effort()
        assert os.environ["STRIX_REASONING_EFFORT"] == "high"

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("STRIX_REASONING_EFFORT", "superhigh")
        from strix_telegram_bot.config import _normalize_reasoning_effort
        with pytest.raises(RuntimeError, match="STRIX_REASONING_EFFORT"):
            _normalize_reasoning_effort()

    def test_all_valid_values_accepted(self, monkeypatch):
        from strix_telegram_bot.config import _VALID_REASONING_EFFORTS, _normalize_reasoning_effort
        for effort in _VALID_REASONING_EFFORTS:
            monkeypatch.setenv("STRIX_REASONING_EFFORT", effort.upper())
            _normalize_reasoning_effort()
            assert os.environ["STRIX_REASONING_EFFORT"] == effort
