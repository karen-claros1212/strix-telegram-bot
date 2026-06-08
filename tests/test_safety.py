"""Test safety and security modules."""

from __future__ import annotations

from strix_telegram_bot.safety.redaction import redact_text, redact_json
from strix_telegram_bot.safety.scope_policy import validate_scope, classify_target
from strix_telegram_bot.safety.attachment_policy import validate_attachment, sanitize_target
from strix_telegram_bot.safety.approval_gate import get_gate


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
    def test_reject_localhost(self):
        ok, msg = validate_scope(["http://localhost:8080"])
        assert ok is False
        assert "internal" in msg or "Scope" in msg

    def test_reject_private_ip(self):
        ok, msg = validate_scope(["http://192.168.1.1"])
        assert ok is False

    def test_accept_public_url(self):
        ok, msg = validate_scope(["https://example.com"])
        assert ok is True

    def test_max_targets(self):
        ok, msg = validate_scope([f"https://x{i}.com" for i in range(10)])
        assert ok is False

    def test_classify_web(self):
        assert classify_target("https://example.com") == "web"

    def test_classify_repo(self):
        assert classify_target("https://github.com/user/repo") == "repo"


class TestAttachmentPolicy:
    def test_accept_txt(self):
        ok, msg = validate_attachment("notes.txt", 1000)
        assert ok is True

    def test_reject_exe(self):
        ok, msg = validate_attachment("virus.exe", 1000)
        assert ok is False

    def test_reject_too_large(self):
        ok, msg = validate_attachment("big.txt", 100 * 1024 * 1024)
        assert ok is False

    def test_sanitize_valid(self):
        ok, msg = sanitize_target("https://example.com")
        assert ok is True

    def test_sanitize_invalid_chars(self):
        ok, msg = sanitize_target("https://example.com; rm -rf /")
        assert ok is False


class TestApprovalGate:
    def test_request_and_resolve(self):
        gate = get_gate()
        req_id = gate.request_approval(
            job_run_name="test", target=["x"],
            mode="deep", reason="test",
            chat_id=1, message_id=1,
        )
        assert req_id == "test"
        assert gate.get_pending("test") is not None

        result = gate.resolve("test", True)
        assert result is not None
        assert result.resolved is True
        assert gate.get_pending("test") is None
