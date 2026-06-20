"""Test Telegram API helper layer (no network — structure only)."""

from __future__ import annotations

from strix_telegram_bot.telegram import (
    _api_url,
    _request,
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


def test_request_has_request_timeout():
    """_request accepts configurable request_timeout parameter."""
    import inspect
    sig = inspect.signature(_request)
    params = list(sig.parameters.keys())
    assert "request_timeout" in params


def test_get_updates_uses_single_retry():
    """get_updates calls _request with retries=1."""
    import inspect
    from strix_telegram_bot.telegram import get_updates
    source = inspect.getsource(get_updates)
    assert "retries=1" in source


def test_get_updates_request_timeout_greater_than_long_poll():
    """get_updates passes request_timeout = long_poll_timeout + 10."""
    import inspect
    from strix_telegram_bot.telegram import get_updates
    source = inspect.getsource(get_updates)
    assert "timeout + 10" in source


class TestSanitizeAgentContent:
    """Content sanitizer strips base64, data URLs, and internal paths."""

    @staticmethod
    def _sanitize(content: str) -> str:
        from strix_telegram_bot.bot import StrixBot
        return StrixBot._sanitize_agent_content(content)

    def test_strips_data_image_url(self):
        content = "Here is a screenshot: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        result = self._sanitize(content)
        assert "data:image" not in result
        assert "[imagen]" in result
        assert "iVBOR" not in result

    def test_strips_data_url(self):
        content = "Binary: data:application/octet-stream;base64,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
        result = self._sanitize(content)
        assert "data:" not in result
        assert "[datos binarios]" in result

    def test_strips_sandbox_paths(self):
        content = "Saved to /home/jesus/strix-telegram-bot/strix_runs/scan-abc12345/output.txt"
        result = self._sanitize(content)
        assert "/home/jesus" not in result
        assert "[sandbox]/scan-abc12345" in result

    def test_strips_long_internal_paths(self):
        content = "File at /sandbox/verylongpaththatexceedstwentycharacters/output.json"
        result = self._sanitize(content)
        assert "verylongpath" not in result
        assert "[ruta interna]" in result

    def test_preserves_normal_text(self):
        content = "The scan found 3 open ports on example.com."
        result = self._sanitize(content)
        assert result == content

    def test_preserves_short_base64(self):
        content = "Short base64: data:image/png;base64,abc123"
        result = self._sanitize(content)
        assert "data:image/png;base64,abc123" in result  # too short to match
