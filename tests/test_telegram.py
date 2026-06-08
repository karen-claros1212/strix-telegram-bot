"""Test Telegram API helper layer (no network — structure only)."""

from __future__ import annotations

from strix_telegram_bot.telegram import (
    _api_url,
    send_message,
    edit_message,
    answer_callback,
    send_chat_action,
)


def test_api_url_format():
    url = _api_url("getMe")
    assert "getMe" in url
    assert url.endswith("/getMe")

    url = _api_url("sendMessage")
    assert url.endswith("/sendMessage")
