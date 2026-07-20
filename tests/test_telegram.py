"""Test Telegram API helper layer (no network — structure only)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# ── Fix 2/4: _updates_offset persistence (atomic write, .bot-state, JSON, fsync) ──
class TestUpdatesOffsetPersistence:
    def test_load_offset_from_existing_file(self, tmp_path):
        """Should load offset from strix_runs/.bot-state/telegram_offset.json."""
        import json as _json
        from strix_telegram_bot.bot import StrixBot
        state_dir = tmp_path / "strix_runs" / ".bot-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "telegram_offset.json").write_text(_json.dumps({"offset": 42}))

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
        """Should return None for non-JSON or missing offset key."""
        import json as _json
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        state_dir = tmp_path / "strix_runs" / ".bot-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "telegram_offset.json").write_text(_json.dumps({"no_offset": 1}))

        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            offset = bot._load_offset()
            assert offset is None
        finally:
            settings.strix_runs_dir = old_dir

    def test_save_offset_creates_atomic_file_with_fsync(self, tmp_path):
        """Should persist offset atomically via tmp + fsync + replace."""
        import json as _json
        from strix_telegram_bot.bot import StrixBot
        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"
        try:
            bot = StrixBot.__new__(StrixBot)
            bot._updates_offset = 100
            bot._save_offset()

            state_dir = tmp_path / "strix_runs" / ".bot-state"
            offset_file = state_dir / "telegram_offset.json"
            tmp_file = state_dir / ".telegram_offset.json.tmp"
            assert offset_file.exists()
            assert not tmp_file.exists()  # tmp should be gone after replace
            data = _json.loads(offset_file.read_text())
            assert data["offset"] == 100
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

            state_dir = tmp_path / "strix_runs" / ".bot-state"
            offset_file = state_dir / "telegram_offset.json"
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


# ── Fix 1: Target deduplication — same URL during active scan ──
class TestTargetDeduplication:
    def _make_bot_with_active_scan(self, targets):
        from strix_telegram_bot.bot import StrixBot
        from unittest.mock import MagicMock
        bot = StrixBot.__new__(StrixBot)
        bridge = MagicMock()
        bridge.is_running = True
        bridge._current_targets = targets
        bridge._run_name = "scan-abc12345"
        bridge._preferred_agent_id = None
        bridge.root_agent_id = "agent-1"
        bot._bridge = bridge
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
        return bot, bridge

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_same_target_no_instruction_deduplicates(self, mock_send, mock_sca):
        """Same target + no instruction → 'Ese objetivo ya está siendo analizado'."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view?usp=drivesdk"]
        )
        update = {"message": {
            "chat": {"id": 123},
            "text": "https://drive.google.com/file/d/1abc/view?usp=drivesdk",
        }}
        bot._handle_text_message(update)

        bridge.send_message_to_agent.assert_not_called()
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][2]
        assert "ya está siendo analizado" in sent_text
        assert "scan-abc12345" in sent_text

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_same_target_with_instruction_forwarded(self, mock_send, mock_sca):
        """Same target + instruction → forwarded to agent."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        update = {"message": {
            "chat": {"id": 123},
            "text": "https://drive.google.com/file/d/1abc/view prioriza el manifiesto",
        }}
        bot._handle_text_message(update)

        bridge.send_message_to_agent.assert_called_once()
        mock_send.assert_not_called()

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_different_target_forwarded(self, mock_send, mock_sca):
        """Different target during scan → forwarded to agent."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        update = {"message": {
            "chat": {"id": 123},
            "text": "https://example.com",
        }}
        bot._handle_text_message(update)

        bridge.send_message_to_agent.assert_called_once()
        mock_send.assert_not_called()

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_url_with_trailing_punctuation_deduplicates(self, mock_send, mock_sca):
        """URL ending in period/comma is recognized as duplicate."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        update = {"message": {
            "chat": {"id": 123},
            "text": "https://drive.google.com/file/d/1abc/view.",
        }}
        bot._handle_text_message(update)

        bridge.send_message_to_agent.assert_not_called()
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][2]
        assert "ya está siendo analizado" in sent_text

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_natural_language_forwarded(self, mock_send, mock_sca):
        """Natural language without targets → forwarded to agent."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        update = {"message": {
            "chat": {"id": 123},
            "text": "Continúa con el análisis",
        }}
        bot._handle_text_message(update)

        bridge.send_message_to_agent.assert_called_once()
        mock_send.assert_not_called()
