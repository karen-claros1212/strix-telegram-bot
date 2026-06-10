"""Test health commands — version parsing, compatibility checks."""

from __future__ import annotations

from strix_telegram_bot.commands.health import _parse_version, _version_warning


class TestVersionParsing:
    def test_parse_version_full(self):
        assert _parse_version("strix-agent 1.0.4") == (1, 0, 4)

    def test_parse_version_patch(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_parse_version_major_minor(self):
        assert _parse_version("2.1.0") == (2, 1, 0)
        assert _parse_version("3.0.5") == (3, 0, 5)

    def test_parse_version_unknown(self):
        assert _parse_version("") == (0, 0, 0)
        assert _parse_version("not-a-version") == (0, 0, 0)


class TestVersionWarning:
    def test_no_warning_when_current(self):
        warning = _version_warning("1.0.4", "3.12.0")
        assert warning == ""

    def test_no_warning_when_newer(self):
        warning = _version_warning("1.1.0", "3.13.0")
        assert warning == ""

    def test_warning_strix_outdated(self):
        warning = _version_warning("1.0.2", "3.12.0")
        assert "outdated" in warning
        assert "1.0.2" in warning
        assert "1.0.4" in warning

    def test_warning_python_outdated(self):
        warning = _version_warning("1.0.5", "3.10.0")
        assert "below STRIX minimum" in warning
        assert "3.10" in warning

    def test_warning_both_outdated(self):
        warning = _version_warning("1.0.1", "3.11.0")
        assert "outdated" in warning
        assert "below STRIX minimum" in warning
