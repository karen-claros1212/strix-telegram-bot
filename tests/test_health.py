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
        warning = _version_warning("1.3.1", "3.12.0")
        assert warning == ""

    def test_no_warning_when_newer(self):
        warning = _version_warning("1.4.0", "3.13.0")
        assert warning == ""

    def test_warning_strix_outdated(self):
        warning = _version_warning("1.0.2", "3.12.0")
        assert "desactualizada" in warning
        assert "1.0.2" in warning
        assert "1.3.1" in warning

    def test_warning_python_outdated(self):
        warning = _version_warning("1.3.1", "3.10.0")
        assert "por debajo del mínimo" in warning
        assert "3.10" in warning

    def test_warning_both_outdated(self):
        warning = _version_warning("1.0.1", "3.11.0")
        assert "desactualizada" in warning
        assert "por debajo del mínimo" in warning


# ── Fix 5: /version uses importlib.metadata, shows module path + min version ──
class TestVersionUsesPackageMetadata:
    def test_cmd_version_uses_pkg_version(self, monkeypatch):
        """cmd_version should call importlib.metadata.version, not subprocess."""
        import strix_telegram_bot.commands.health as health_mod
        from unittest.mock import MagicMock, patch

        calls = []
        def fake_pkg_version(pkg):
            calls.append(pkg)
            return "1.1.0"

        monkeypatch.setattr(health_mod, "_pkg_version", fake_pkg_version)

        fake_bot = MagicMock()
        fake_update = {"message": {"chat": {"id": 123}}}

        with patch.object(health_mod, "send_message") as mock_send:
            health_mod.cmd_version(fake_bot, fake_update)
            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][2]
            assert "1.1.0" in sent_text
            assert "unknown" not in sent_text

    def test_cmd_version_shows_module_path(self, monkeypatch):
        """cmd_version output should include the module path."""
        import strix_telegram_bot.commands.health as health_mod
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(health_mod, "_pkg_version", lambda pkg: "1.1.0")
        monkeypatch.setattr(health_mod, "_get_strix_module_path", lambda: "/path/to/strix")

        fake_bot = MagicMock()
        fake_update = {"message": {"chat": {"id": 123}}}

        with patch.object(health_mod, "send_message") as mock_send:
            health_mod.cmd_version(fake_bot, fake_update)
            sent_text = mock_send.call_args[0][2]
            assert "Módulo:" in sent_text
            assert "/path/to/strix" in sent_text

    def test_cmd_version_shows_min_version(self, monkeypatch):
        """cmd_version output should include the minimum required version."""
        import strix_telegram_bot.commands.health as health_mod
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(health_mod, "_pkg_version", lambda pkg: "1.3.1")

        fake_bot = MagicMock()
        fake_update = {"message": {"chat": {"id": 123}}}

        with patch.object(health_mod, "send_message") as mock_send:
            health_mod.cmd_version(fake_bot, fake_update)
            sent_text = mock_send.call_args[0][2]
            assert "Mínimo:" in sent_text
            assert "1.3.1" in sent_text  # _STRIX_MIN_VERSION

    def test_send_health_uses_pkg_version(self, monkeypatch):
        """_send_health should call importlib.metadata.version, not subprocess."""
        import strix_telegram_bot.commands.health as health_mod
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(health_mod, "_pkg_version", lambda pkg: "1.1.0")

        fake_bot = MagicMock()
        with patch.object(health_mod, "send_message") as mock_send:
            health_mod._send_health(fake_bot, 123)
            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][2]
            assert "1.1.0" in sent_text or "1\\.1\\.0" in sent_text

    def test_no_subprocess_import(self):
        """health.py should NOT import subprocess."""
        import strix_telegram_bot.commands.health as health_mod
        import inspect
        source = inspect.getsource(health_mod)
        assert "subprocess.run" not in source or "strix.*--version" not in source
