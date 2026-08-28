"""Tests for send_document, _build_multipart_form, _sanitize_filename, _is_permanent_client_error.

Mocks only urllib.request.urlopen — NOT send_document itself.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture
def mock_urlopen():
    with patch("strix_telegram_bot.telegram.urllib.request.urlopen") as m:
        yield m


class TestSanitizeFilename:
    def test_basename(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename("/some/path/file.md") == "file.md"

    def test_strips_crlf(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename("report\r\n.md") == "report.md"

    def test_removes_quotes(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename('"report".md') == "report.md"

    def test_keeps_safe_chars(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename(
            "STRIX_scan-123_INFORME_COMPLETO.md"
        ) == "STRIX_scan-123_INFORME_COMPLETO.md"

    def test_fallback_when_empty(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename("") == "report.md"

    def test_appends_md_extension(self):
        from strix_telegram_bot.telegram import _sanitize_filename
        assert _sanitize_filename("report") == "report.md"


class TestIsPermanentClientError:
    def test_400_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(400) is True

    def test_401_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(401) is True

    def test_403_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(403) is True

    def test_404_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(404) is True

    def test_405_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(405) is True

    def test_409_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(409) is True

    def test_413_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(413) is True

    def test_422_is_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(422) is True

    def test_429_not_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(429) is False

    def test_500_not_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(500) is False

    def test_408_not_permanent(self):
        from strix_telegram_bot.telegram import _is_permanent_client_error
        assert _is_permanent_client_error(408) is False


class TestBuildMultipartForm:
    def test_contains_chat_id(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "12345"},
            {"document": ("file.md", b"# content", "text/markdown")},
        )
        assert b'name="chat_id"' in body
        assert b"12345" in body

    def test_contains_caption(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1", "caption": "Test caption"},
            {"document": ("f.md", b"data", "text/markdown")},
        )
        assert b"Test caption" in body

    def test_content_disposition_document(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1"},
            {"document": ("report.md", b"hello", "text/markdown")},
        )
        assert b'name="document"' in body
        assert b'filename="report.md"' in body

    def test_content_type_markdown(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1"},
            {"document": ("f.md", b"x", "text/markdown")},
        )
        assert b"Content-Type: text/markdown" in body

    def test_file_bytes_in_body(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1"},
            {"document": ("f.md", b"hello world", "text/markdown")},
        )
        assert b"hello world" in body

    def test_boundary_format(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1"},
            {"document": ("f.md", b"x", "text/markdown")},
        )
        assert content_type.startswith("multipart/form-data; boundary=")
        assert body.startswith(b"------strixFormBoundary")
        boundary = body.split(b"------strixFormBoundary")[1].split(b"\r\n")[0].split(b"--")[0]
        assert body.rstrip().endswith(b"------strixFormBoundary" + boundary + b"--")

    def test_safe_filename_used(self):
        from strix_telegram_bot.telegram import _build_multipart_form
        body, content_type = _build_multipart_form(
            {"chat_id": "1"},
            {"document": ('"bad" name\r\n.md', b"x", "text/markdown")},
        )
        assert b'filename="bad name.md"' in body


class TestSendDocument:
    def test_file_not_found_is_permanent(self):
        from strix_telegram_bot.telegram import send_document
        result = send_document(None, 12345, "/nonexistent/file.md")
        assert result.ok is False
        assert result.kind == "permanent"

    def test_empty_file_is_permanent(self, tmp_path):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "empty.md"
        p.write_text("")
        result = send_document(None, 12345, str(p))
        assert result.ok is False
        assert result.kind == "permanent"

    def test_success_returns_message_id(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "report.md"
        p.write_text("# Report\n\nBody")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"ok": True, "result": {"message_id": 42}}
        ).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = send_document(None, 12345, str(p))
        assert result.ok is True
        assert result.kind == "success"
        assert result.result == {"message_id": 42}
        assert result.message_id == 42
        mock_urlopen.assert_called_once()

    def test_caption_included(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_document(None, 12345, str(p), caption="My caption")
        sent_data = mock_urlopen.call_args[0][0].data
        assert b"My caption" in sent_data

    def test_filename_param_used(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_document(None, 12345, str(p), filename="STRIX_scan-001_INFORME_COMPLETO.md")
        sent_data = mock_urlopen.call_args[0][0].data
        assert b"STRIX_scan-001_INFORME_COMPLETO.md" in sent_data

    def test_reply_markup_serialized(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_document(
            None, 12345, str(p),
            reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]},
        )
        sent_data = mock_urlopen.call_args[0][0].data
        assert b"inline_keyboard" in sent_data

    def test_api_returns_not_ok_returns_none(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"ok": False, "error_code": 400, "description": "Bad Request"}
        ).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = send_document(None, 12345, str(p))
        assert result.ok is False
        assert result.kind == "permanent"

    def test_http_400_not_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_urlopen.side_effect = HTTPError(
            "http://example.com", 400, "Bad Request", {}, None
        )

        result = send_document(None, 12345, str(p))
        assert result.kind == "permanent"
        assert mock_urlopen.call_count == 1

    def test_http_401_not_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_urlopen.side_effect = HTTPError(
            "http://example.com", 401, "Unauthorized", {}, None
        )

        result = send_document(None, 12345, str(p))
        assert result.kind == "permanent"
        assert mock_urlopen.call_count == 1

    def test_http_403_not_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")
        mock_urlopen.side_effect = HTTPError("http://ex.com", 403, "Forbidden", {}, None)
        result = send_document(None, 12345, str(p))
        assert result.kind == "permanent"
        assert mock_urlopen.call_count == 1

    def test_http_500_retried_once(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_urlopen.side_effect = HTTPError(
            "http://ex.com", 500, "Internal Error", {}, None
        )

        result = send_document(None, 12345, str(p))
        assert result.kind == "transient"
        assert mock_urlopen.call_count == 3  # max retries

    def test_http_408_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")
        mock_urlopen.side_effect = HTTPError("http://ex.com", 408, "Timeout", {}, None)
        result = send_document(None, 12345, str(p))
        assert result.kind == "transient"
        assert mock_urlopen.call_count == 3

    def test_network_unreachable_not_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")
        mock_urlopen.side_effect = URLError("Network is unreachable")
        result = send_document(None, 12345, str(p))
        assert result.kind == "transient"
        assert mock_urlopen.call_count == 1

    def test_dns_failure_not_retried(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")
        mock_urlopen.side_effect = URLError("Name or service not known")
        result = send_document(None, 12345, str(p))
        assert result.kind == "transient"
        assert mock_urlopen.call_count == 1

    def test_json_decode_error_returns_none(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = send_document(None, 12345, str(p))
        assert result.kind == "transient"

    def test_content_type_header_is_multipart(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_document(None, 12345, str(p))
        req = mock_urlopen.call_args[0][0]
        ct = req.headers.get("Content-type", "")
        assert "multipart/form-data" in ct

    def test_mime_type_text_markdown(self, tmp_path, mock_urlopen):
        from strix_telegram_bot.telegram import send_document
        p = tmp_path / "r.md"
        p.write_text("data")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_document(None, 12345, str(p))
        sent_data = mock_urlopen.call_args[0][0].data
        assert b"Content-Type: text/markdown" in sent_data
