"""Test Telegram API helper layer (no network — structure only)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from strix_telegram_bot.telegram import (
    _api_url,
    _request,
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
        content = (
            "Here is a screenshot: data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        result = self._sanitize(content)
        assert "data:image" not in result
        assert "[imagen]" in result
        assert "iVBOR" not in result

    def test_strips_data_url(self):
        content = (
            "Binary: data:application/octet-stream;base64,"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
        )
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


# ── Read-only chat: during an active scan no message reaches the agent ──
class TestReadOnlyChatDuringScan:
    """Spec 5.2 (FASE 1): while a scan runs and NO agent is waiting for the
    user, the chat is read-only. Every message receives the fixed response
    and nothing is forwarded to Strix."""

    def _make_bot_with_active_scan(self, targets):
        from unittest.mock import MagicMock

        from strix_telegram_bot.bot import StrixBot
        bot = StrixBot.__new__(StrixBot)
        bridge = MagicMock()
        bridge.is_running = True
        bridge._current_targets = targets
        bridge._run_name = "scan-abc12345"
        bridge._preferred_agent_id = None
        bridge.root_agent_id = "agent-1"
        bridge.awaiting_user_agents.return_value = []
        bot._bridge = bridge
        bot._chat_fragments = {}
        bot._chat_fragment_count = {}
        bot._chat_event_version = {}
        bot._tool_message_ids = {}
        bot._active_chat_agent_id = None
        bot._active_chat_message_id = None
        bot._active_chat_chat_id = None
        bot._notified_waiting_agents = set()
        bot._pending_reply_agent_id = None
        bot._command_handlers = {}
        bot._callback_handlers = {}
        bot._last_panel_text = ""
        return bot, bridge

    def _assert_readonly_reply(self, mock_send, bot, bridge, text):
        update = {"message": {"chat": {"id": 123}, "text": text}}
        bot._handle_text_message(update)
        bridge.send_message.assert_not_called()
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][2]
        assert "El análisis está en curso." in sent_text
        assert "Ningún agente espera tu respuesta ahora." in sent_text
        assert mock_send.call_args.kwargs.get("disable_web_page_preview") is True

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_url_only_receives_readonly_reply(self, mock_send, mock_sca):
        """A bare URL during scan → read-only reply, never forwarded."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        self._assert_readonly_reply(
            mock_send, bot, bridge,
            "https://drive.google.com/file/d/1abc/view",
        )

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_url_with_instruction_receives_readonly_reply(self, mock_send, mock_sca):
        """URL + instruction during scan → read-only reply, never forwarded."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        self._assert_readonly_reply(
            mock_send, bot, bridge,
            "https://drive.google.com/file/d/1abc/view prioriza el manifiesto",
        )

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_different_target_receives_readonly_reply(self, mock_send, mock_sca):
        """A different URL during scan → read-only reply, never forwarded."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        self._assert_readonly_reply(mock_send, bot, bridge, "https://example.com")

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_url_with_trailing_punctuation_receives_readonly_reply(self, mock_send, mock_sca):
        """URL ending in period during scan → read-only reply, never forwarded."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        self._assert_readonly_reply(
            mock_send, bot, bridge,
            "https://drive.google.com/file/d/1abc/view.",
        )

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_natural_language_receives_readonly_reply(self, mock_send, mock_sca):
        """Natural language during scan → read-only reply, never forwarded."""
        bot, bridge = self._make_bot_with_active_scan(
            ["https://drive.google.com/file/d/1abc/view"]
        )
        self._assert_readonly_reply(mock_send, bot, bridge, "Continúa con el análisis")


class TestAwaitingUserFlow:
    """FASE 1: AWAITING_USER end-to-end.

    - exactly one agent parked with wait_kind == 'user' → the user's text is
      routed to that agent via bridge.send_message (delegates to the official
      send_user_message_to_agent)
    - several agents waiting → selection keyboard; after picking, the user's
      text is routed to the chosen agent
    """

    def _make_bot_with_active_scan(self, waiting):
        from strix_telegram_bot.bot import StrixBot
        bot = StrixBot.__new__(StrixBot)
        bridge = MagicMock()
        bridge.is_running = True
        bridge._run_name = "scan-abc12345"
        bridge.awaiting_user_agents.return_value = waiting
        bridge.send_message.return_value = True
        bot._bridge = bridge
        bot._chat_fragments = {}
        bot._chat_fragment_count = {}
        bot._chat_event_version = {}
        bot._tool_message_ids = {}
        bot._active_chat_agent_id = None
        bot._active_chat_message_id = None
        bot._active_chat_chat_id = None
        bot._notified_waiting_agents = set()
        bot._pending_reply_agent_id = None
        bot._command_handlers = {}
        bot._callback_handlers = {}
        bot._last_panel_text = ""
        return bot, bridge

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_single_waiting_agent_receives_user_reply(self, mock_send, mock_sca):
        """One agent waiting for user → text routed to that agent."""
        waiting = [{"id": "agent-1", "name": "Root"}]
        bot, bridge = self._make_bot_with_active_scan(waiting)
        update = {"message": {"chat": {"id": 123}, "text": "Sí, continúa"}}
        bot._handle_text_message(update)
        bridge.send_message.assert_called_once_with("agent-1", "Sí, continúa")
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[0][2]
        assert "Respuesta enviada a Root." in sent_text

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_multiple_waiting_agents_shows_selection(self, mock_send, mock_sca):
        """Several agents waiting → selection keyboard, nothing forwarded yet."""
        waiting = [
            {"id": "agent-1", "name": "Root", "status": "waiting"},
            {"id": "agent-2", "name": "Scanner", "status": "waiting"},
        ]
        bot, bridge = self._make_bot_with_active_scan(waiting)
        update = {"message": {"chat": {"id": 123}, "text": "¿Qué necesitas?"}}
        bot._handle_text_message(update)
        bridge.send_message.assert_not_called()
        assert mock_send.call_count == 1
        sent_text = mock_send.call_args[0][2]
        assert "Varios agentes esperan tu respuesta" in sent_text
        keyboard = mock_send.call_args.kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = [
            b["callback_data"]
            for row in keyboard["inline_keyboard"]
            for b in row
        ]
        assert "agent:agent-1" in buttons
        assert "agent:agent-2" in buttons

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_selection_routes_reply_to_chosen_agent(self, mock_send, mock_sca):
        """After picking an agent, the next text goes to that agent only."""
        waiting = [
            {"id": "agent-1", "name": "Root", "status": "waiting"},
            {"id": "agent-2", "name": "Scanner", "status": "waiting"},
        ]
        bot, bridge = self._make_bot_with_active_scan(waiting)
        bot._pending_reply_agent_id = "agent-2"
        update = {"message": {"chat": {"id": 123}, "text": "Usa el token X"}}
        bot._handle_text_message(update)
        bridge.send_message.assert_called_once_with("agent-2", "Usa el token X")
        assert bot._pending_reply_agent_id is None
        sent_text = mock_send.call_args[0][2]
        assert "Respuesta enviada a Scanner." in sent_text

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_send_failure_reports_error(self, mock_send, mock_sca):
        """If the reply cannot be delivered, the user is told to retry."""
        waiting = [{"id": "agent-1", "name": "Root"}]
        bot, bridge = self._make_bot_with_active_scan(waiting)
        bridge.send_message.return_value = False
        update = {"message": {"chat": {"id": 123}, "text": "Hola"}}
        bot._handle_text_message(update)
        bridge.send_message.assert_called_once_with("agent-1", "Hola")
        sent_text = mock_send.call_args[0][2]
        assert "No se pudo entregar tu respuesta" in sent_text


class TestHandleDocumentUpload:
    """FASE 6: Telegram upload flow (_handle_document) — robust + Spanish."""

    def _make_bot(self, is_running=False, run_name=None, menu_state=None):
        from strix_telegram_bot.bot import StrixBot
        bot = StrixBot.__new__(StrixBot)
        bridge = MagicMock()
        bridge.is_running = is_running
        bridge.run_name = run_name
        bot._bridge = bridge
        bot._launch_scan = MagicMock()
        return bot, bridge

    @patch("strix_telegram_bot.bot.get_panel_manager")
    @patch("strix_telegram_bot.telegram.get_file")
    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_document_upload_not_running_saves_and_prompts(
        self, mock_send, mock_sca, mock_get_file, mock_pm, monkeypatch, tmp_path
    ):
        """A document upload while idle is saved to .bot-uploads/ (not strix_runs)."""
        from strix_telegram_bot.models import MenuState

        monkeypatch.setattr("strix_telegram_bot.config.settings.bot_dir", tmp_path)
        bot, _ = self._make_bot(is_running=False)
        mock_get_file.return_value = b"file-bytes"
        mock_pm.return_value.current = MenuState.MAIN

        update = {
            "message": {"chat": {"id": 1}, "document": {"file_id": "fid", "file_name": "f.txt"}}
        }
        bot._handle_document(update)

        # File stored in .bot-uploads/ (OUTSIDE strix_runs)
        uploads = tmp_path / ".bot-uploads"
        assert uploads.is_dir()
        files = list(uploads.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith("_f.txt")
        assert files[0].read_bytes() == b"file-bytes"
        # Nothing written under strix_runs/upload/
        assert not (tmp_path / "strix_runs" / "upload").exists()
        sent = [c.args[2] for c in mock_send.call_args_list]
        assert any("Archivo guardado" in t and "Escanear" in t for t in sent)

    @patch("strix_telegram_bot.bot.get_panel_manager")
    @patch("strix_telegram_bot.telegram.get_file")
    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_document_upload_while_running_rejects(
        self, mock_send, mock_sca, mock_get_file, mock_pm, monkeypatch, tmp_path
    ):
        """A document upload while a scan runs is REJECTED (no write anywhere).

        Strix already fixed its targets via prepare_run, so a new file won't
        enter the analysis. Rejecting keeps the mirror passive — no write into
        the official Strix run nor the bot's private upload dir.
        """
        from strix_telegram_bot.models import MenuState

        monkeypatch.setattr("strix_telegram_bot.config.settings.bot_dir", tmp_path)
        bot, _ = self._make_bot(is_running=True, run_name="scan-abc")
        mock_get_file.return_value = b"file-bytes"
        mock_pm.return_value.current = MenuState.MAIN

        update = {
            "message": {"chat": {"id": 1}, "document": {"file_id": "fid", "file_name": "f.txt"}}
        }
        bot._handle_document(update)

        # Rejected before any write — no .bot-uploads/ dir created
        assert not (tmp_path / ".bot-uploads").exists()
        sent = [c.args[2] for c in mock_send.call_args_list]
        assert any("ya está en curso" in t for t in sent)
        assert any("no puede incorporar" in t for t in sent)

    @patch("strix_telegram_bot.bot.get_panel_manager")
    @patch("strix_telegram_bot.telegram.get_file")
    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_document_upload_waiting_for_targets_launches_scan(
        self, mock_send, mock_sca, mock_get_file, mock_pm, monkeypatch, tmp_path
    ):
        """A document upload while waiting for targets launches a scan on it.

        The scan target is the bot-private .bot-uploads/ path (valid, absolute).
        """
        from strix_telegram_bot.models import MenuState

        monkeypatch.setattr("strix_telegram_bot.config.settings.bot_dir", tmp_path)
        bot, _ = self._make_bot(is_running=False)
        mock_get_file.return_value = b"file-bytes"
        mock_pm.return_value.current = MenuState.WAITING_FOR_TARGETS

        update = {
            "message": {"chat": {"id": 1}, "document": {"file_id": "fid", "file_name": "f.txt"}}
        }
        bot._handle_document(update)

        bot._launch_scan.assert_called_once()
        args = bot._launch_scan.call_args
        target = args[0][1][0]
        # The target is a valid absolute path inside .bot-uploads/ (not strix_runs)
        uploads = tmp_path / ".bot-uploads"
        assert uploads.is_dir()
        assert target.startswith(str(uploads))
        assert Path(target).is_file()
        assert Path(target).read_bytes() == b"file-bytes"

    @patch("strix_telegram_bot.bot.get_panel_manager")
    @patch("strix_telegram_bot.telegram.get_file")
    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_photo_upload_uses_default_name(
        self, mock_send, mock_sca, mock_get_file, mock_pm, monkeypatch, tmp_path
    ):
        """A photo upload (no file_name) is stored with the photo.jpg suffix."""
        from strix_telegram_bot.models import MenuState

        monkeypatch.setattr("strix_telegram_bot.config.settings.bot_dir", tmp_path)
        bot, _ = self._make_bot(is_running=False)
        mock_get_file.return_value = b"img-bytes"
        mock_pm.return_value.current = MenuState.MAIN

        update = {"message": {"chat": {"id": 1}, "photo": [{"file_id": "fid"}]}}
        bot._handle_document(update)

        uploads = tmp_path / ".bot-uploads"
        files = list(uploads.iterdir())
        assert len(files) == 1
        # Default name photo.jpg is preserved (with a unique prefix)
        assert files[0].name.endswith("_photo.jpg")
        assert files[0].read_bytes() == b"img-bytes"

    @patch("strix_telegram_bot.telegram.get_file")
    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_download_failure_reports_error(self, mock_send, mock_sca, mock_get_file):
        """If get_file returns None, the user is told the download failed."""
        bot, _ = self._make_bot(is_running=False)
        mock_get_file.return_value = None
        update = {
            "message": {"chat": {"id": 1}, "document": {"file_id": "fid", "file_name": "f.txt"}}
        }
        bot._handle_document(update)
        sent = [c.args[2] for c in mock_send.call_args_list]
        assert any("Error al descargar" in t for t in sent)

    @patch("strix_telegram_bot.telegram.send_chat_action")
    @patch("strix_telegram_bot.bot.send_message")
    def test_no_attachment_reports_error(self, mock_send, mock_sca):
        """A message with neither document nor photo reports a read error."""
        bot, _ = self._make_bot(is_running=False)
        update = {"message": {"chat": {"id": 1}, "text": "hola"}}
        bot._handle_document(update)
        sent = [c.args[2] for c in mock_send.call_args_list]
        assert any("No se pudo leer el archivo" in t for t in sent)


class TestUploadStorageSeparation:
    """_store_upload_bytes: bot-private storage, sanitized, no collisions/traversal.

    The destination is settings.bot_dir / ".bot-uploads" (OUTSIDE strix_runs,
    which is reserved for Strix-produced runs/evidence).
    """

    def _store(self, monkeypatch, tmp_path, file_name, data=b"data"):
        from strix_telegram_bot.bot import _store_upload_bytes
        monkeypatch.setattr("strix_telegram_bot.config.settings.bot_dir", tmp_path)
        return _store_upload_bytes(data, file_name)

    def test_normal_upload_goes_to_bot_uploads(self, monkeypatch, tmp_path):
        """A. document.pdf -> under .bot-uploads/, nothing under strix_runs/upload/."""
        path = self._store(monkeypatch, tmp_path, "document.pdf")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        assert uploads.is_dir()
        assert path.parent == uploads
        assert path.name.endswith("_document.pdf")
        assert path.read_bytes() == b"data"
        # Nothing written under strix_runs/upload/
        assert not (tmp_path / "strix_runs" / "upload").exists()

    def test_same_filename_twice_no_overwrite(self, monkeypatch, tmp_path):
        """B. same filename twice -> both survive (two distinct files)."""
        p1 = self._store(monkeypatch, tmp_path, "document.pdf")
        p2 = self._store(monkeypatch, tmp_path, "document.pdf")
        assert p1 != p2
        assert p1.exists() and p2.exists()
        assert p1.read_bytes() == b"data"
        assert p2.read_bytes() == b"data"
        files = list((tmp_path / ".bot-uploads").iterdir())
        assert len(files) == 2

    def test_malicious_filename_no_traversal(self, monkeypatch, tmp_path):
        """C. ../../foo.apk -> never escapes .bot-uploads/."""
        path = self._store(monkeypatch, tmp_path, "../../foo.apk")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        # The file is inside .bot-uploads/ (the ../.. was neutralized)
        assert uploads in path.parents
        assert path.name.endswith("_foo.apk")

    def test_absolute_filename_no_escape(self, monkeypatch, tmp_path):
        """D. /tmp/foo.apk -> never writes to /tmp (basename used, in .bot-uploads/)."""
        path = self._store(monkeypatch, tmp_path, "/tmp/foo.apk")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        # The file is inside .bot-uploads/ (the absolute path was neutralized)
        assert uploads in path.parents
        assert path.name.endswith("_foo.apk")

    def test_backslash_traversal_neutralized(self, monkeypatch, tmp_path):
        """E. ..\\..\\foo.apk -> backslash separator neutralized (no escape)."""
        path = self._store(monkeypatch, tmp_path, "..\\..\\foo.apk")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        assert uploads in path.parents
        # Backslash is NOT a valid separator in the stored name
        assert "\\" not in path.name
        assert path.name.endswith("_foo.apk")

    def test_windows_absolute_path_neutralized(self, monkeypatch, tmp_path):
        """F. C:\\temp\\foo.apk -> Windows absolute path neutralized (basename only)."""
        path = self._store(monkeypatch, tmp_path, "C:\\temp\\foo.apk")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        assert uploads in path.parents
        assert "\\" not in path.name
        assert path.name.endswith("_foo.apk")

    def test_backslash_dir_components_neutralized(self, monkeypatch, tmp_path):
        """G. foo\\bar\\test.apk -> backslash dir components removed (basename only)."""
        path = self._store(monkeypatch, tmp_path, "foo\\bar\\test.apk")
        assert path is not None
        uploads = tmp_path / ".bot-uploads"
        assert uploads in path.parents
        assert "\\" not in path.name
        assert path.name.endswith("_test.apk")
