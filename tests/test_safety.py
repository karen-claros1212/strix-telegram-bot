"""Test safety and security modules."""

from __future__ import annotations

from strix_telegram_bot.safety.redaction import redact_text, redact_json
from strix_telegram_bot.safety.scope_policy import classify_target
from strix_telegram_bot.safety.attachment_policy import classify_attachment, sanitize_target


class TestRedaction:
    def test_redact_api_key(self):
        text = 'api_key = "sk-1234567890123456789012345678901234567890"'
        result = redact_text(text)
        assert "***" in result
        assert "sk-" not in result

    def test_redact_tg_token(self):
        text = "token=12345678:ABCdefghijklmnopqrstuvwxyz1234567890"
        result = redact_text(text)
        assert "***" in result

    def test_redact_private_key(self):
        text = "-----BEGIN PRIVATE KEY-----\nABC123\n-----END PRIVATE KEY-----"
        result = redact_text(text)
        assert "***" in result

    def test_redact_json(self):
        data = {"token": "secret123", "name": "public", "nested": {"api_key": "sk-test"}}
        result = redact_json(data)
        assert result["token"] == "***"
        assert result["name"] == "public"
        assert result["nested"]["api_key"] == "***"


class TestScopePolicy:
    def test_classify_web(self):
        assert classify_target("https://example.com") == "web"

    def test_classify_repo(self):
        assert classify_target("https://github.com/user/repo") == "repo"

    def test_classify_local(self):
        assert classify_target("./app-directory") == "local"

    def test_classify_ip(self):
        assert classify_target("192.168.1.1") == "ip"


class TestAttachmentPolicy:
    def test_classify_txt(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        meta = classify_attachment(f, "notes.txt")
        assert meta["sha256"] is not None
        assert meta["extension"] == ".txt"
        assert meta["size_bytes"] == 5

    def test_classify_exe(self, tmp_path):
        f = tmp_path / "tool.exe"
        f.write_bytes(b"\x00" * 100)
        meta = classify_attachment(f, "tool.exe")
        assert meta["extension"] == ".exe"
        assert meta["sha256"] is not None

    def test_classify_large(self, tmp_path):
        f = tmp_path / "big.pcap"
        data = b"\x00" * (60 * 1024 * 1024)
        f.write_bytes(data)
        meta = classify_attachment(f, "big.pcap")
        assert meta["over_limit_telegram"] is True

    def test_sanitize_valid(self):
        ok, msg = sanitize_target("https://example.com")
        assert ok is True
        assert msg == "OK"

    def test_sanitize_local_path(self):
        ok, msg = sanitize_target("/home/user/app")
        assert ok is True

    def test_sanitize_localhost(self):
        ok, msg = sanitize_target("http://localhost:8080")
        assert ok is True

    def test_sanitize_invalid_chars(self):
        ok, msg = sanitize_target("https://example.com; rm -rf /")
        assert ok is False

    def test_sanitize_empty(self):
        ok, msg = sanitize_target("")
        assert ok is False
