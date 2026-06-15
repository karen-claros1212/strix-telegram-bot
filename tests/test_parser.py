from __future__ import annotations

import pytest
from strix_telegram_bot.bot import StrixBot


@pytest.fixture
def bot():
    import os
    os.environ.setdefault("STRIX_TG_TOKEN", "test:token")
    os.environ.setdefault("STRIX_TG_ALLOWED_USERS", "12345")
    return StrixBot()


class TestExtractTargets:
    def test_urls_only(self, bot):
        targets, instr = bot._extract_targets(
            "Escanea https://kkbrio.xyz y https://kkbrio.co. Son el mismo backend."
        )
        assert targets == ["https://kkbrio.xyz", "https://kkbrio.co"]
        assert "Son el mismo backend" in instr

    def test_urls_with_trailing_punctuation(self, bot):
        targets, instr = bot._extract_targets(
            "Visit https://site.com. It's cool."
        )
        assert targets == ["https://site.com"]
        assert "Visit" in instr

    def test_github_repo(self, bot):
        targets, instr = bot._extract_targets(
            "Check github.com/user/repo for bugs"
        )
        assert "github.com/user/repo" in targets
        assert "Check" in instr

    def test_ip_address(self, bot):
        targets, instr = bot._extract_targets("192.168.1.1")
        assert targets == ["192.168.1.1"]
        assert instr == ""

    def test_domain(self, bot):
        targets, instr = bot._extract_targets("example.com")
        assert targets == ["example.com"]
        assert instr == ""

    def test_greeting_no_target(self, bot):
        targets, instr = bot._extract_targets("hola")
        assert targets == []
        assert instr == "hola"

    def test_mixed_targets_and_instruction(self, bot):
        targets, instr = bot._extract_targets(
            "Escanea https://example.com con nmap y github.com/user/repo con nuclei"
        )
        assert "https://example.com" in targets
        assert "github.com/user/repo" in targets
        assert "con nmap" in instr.lower() or "nmap" in instr
        assert "con nuclei" in instr.lower() or "nuclei" in instr

    def test_empty_text(self, bot):
        targets, instr = bot._extract_targets("")
        assert targets == []
        assert instr == ""

    def test_no_targets_in_instruction(self, bot):
        text = "Revisa la seguridad de este servidor"
        targets, instr = bot._extract_targets(text)
        assert targets == []
        assert instr == text

    def test_url_with_port(self, bot):
        targets, instr = bot._extract_targets("http://localhost:8080")
        assert targets == ["http://localhost:8080"]

    def test_multiline_text(self, bot):
        targets, instr = bot._extract_targets(
            "Escanea:\nhttps://example.com\nhttps://test.org\nTodo con cuidado"
        )
        assert "https://example.com" in targets
        assert "https://test.org" in targets
        assert "Todo con cuidado" in instr

    def test_standalone_domain_is_target(self, bot):
        targets, instr = bot._extract_targets("example.com")
        assert targets == ["example.com"]

    def test_domain_in_running_text_no_comma_is_instruction(self, bot):
        targets, instr = bot._extract_targets("Escanea example.com y revisa todo")
        assert targets == []
        assert "example.com" in instr
