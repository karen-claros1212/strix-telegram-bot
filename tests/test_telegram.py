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


# ── Fix 4: _updates_offset persistence (atomic write, correct path) ──
class TestUpdatesOffsetPersistence:
    def test_load_offset_from_existing_file(self, tmp_path):
        """Should load offset from strix_runs/.updates_offset."""
        from strix_telegram_bot.bot import StrixBot
        offset_file = tmp_path / "strix_runs" / ".updates_offset"
        offset_file.parent.mkdir(parents=True, exist_ok=True)
        offset_file.write_text("42")

        bot = StrixBot.__new__(StrixBot)
        bot._chat_fragments = {}
        bot._chat_fragment_count = {}
        bot._chat_event_version = {}
        bot._tool_message_ids = {}
        bot._active_chat_agent_id = None
        bot._active_chat_message_id = None
        bot._active_chat_chat_id = None
        bot._final_reports_delivered = set()
        bot._command_handlers = {}
        bot._callback_handlers = {}
        bot._last_panel_text = ""

        # Patch settings to use tmp_path
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            offset = bot._load_offset()
            assert offset == 42
        finally:
            settings.strix_runs_dir = old_dir

    def test_load_offset_returns_none_when_file_missing(self, tmp_path):
        """Should return None when no offset file exists."""
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            offset = bot._load_offset()
            assert offset is None
        finally:
            settings.strix_runs_dir = old_dir

    def test_load_offset_returns_none_for_invalid_content(self, tmp_path):
        """Should return None for non-numeric content."""
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        offset_file = tmp_path / "strix_runs" / ".updates_offset"
        offset_file.parent.mkdir(parents=True, exist_ok=True)
        offset_file.write_text("not-a-number")

        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            offset = bot._load_offset()
            assert offset is None
        finally:
            settings.strix_runs_dir = old_dir

    def test_save_offset_creates_atomic_file(self, tmp_path):
        """Should persist offset atomically via tmp + replace."""
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            bot._updates_offset = 100
            bot._save_offset()

            offset_file = tmp_path / "strix_runs" / ".updates_offset"
            tmp_file = tmp_path / "strix_runs" / ".updates_offset.tmp"
            assert offset_file.exists()
            assert not tmp_file.exists()  # tmp should be gone after replace
            assert offset_file.read_text() == "100"
        finally:
            settings.strix_runs_dir = old_dir

    def test_save_offset_none_skips_write(self, tmp_path):
        """Should not write when offset is None."""
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            bot._updates_offset = None
            bot._save_offset()

            offset_file = tmp_path / "strix_runs" / ".updates_offset"
            assert not offset_file.exists()
        finally:
            settings.strix_runs_dir = old_dir

    def test_offset_roundtrip(self, tmp_path):
        """Save and load should roundtrip correctly via settings.strix_runs_dir."""
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            bot._updates_offset = 999
            bot._save_offset()

            bot2 = StrixBot.__new__(StrixBot)
            assert bot2._load_offset() == 999
        finally:
            settings.strix_runs_dir = old_dir
