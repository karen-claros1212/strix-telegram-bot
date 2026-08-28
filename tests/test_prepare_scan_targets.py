"""Test _prepare_scan_targets — archivos adjuntos como copias regulares (no symlinks)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from strix_telegram_bot.bot import StrixBot


class TestPrepareScanTargetsCopiesAsRegularFile:
    def test_prepare_scan_targets_copies_attachment_as_regular_file(self):
        """Verifica que _prepare_scan_targets copia la APK como archivo regular."""
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Crear una APK de prueba con contenido binario determinista
            original_apk = tmp_path / "test.apk"
            original_apk.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 1024)

            test_runs_dir = tmp_path / "strix_runs"
            test_runs_dir.mkdir()

            # Patch settings where it is imported: strix_telegram_bot.config.settings
            with patch("strix_telegram_bot.config.settings") as mock_settings:
                mock_settings.strix_runs_dir = test_runs_dir

                targets = [str(original_apk)]
                prepared_targets, local_sources = bot._prepare_scan_targets(targets)

                # Debe haber al menos un target preparado
                assert len(prepared_targets) >= 1

                # El código hace repos_dir = settings.strix_runs_dir / "repos"
                # Entonces _attachments vive dentro de repos/_attachments/
                attachments_dir = test_runs_dir / "repos" / "_attachments" / "test"
                assert attachments_dir.exists()
                assert attachments_dir.is_dir()

                # La APK copiada debe existir
                copied_apk = attachments_dir / "test.apk"
                assert copied_apk.exists()

                # Debe ser un archivo regular
                assert copied_apk.is_file()

                # No debe ser symlink
                assert not copied_apk.is_symlink()

                # Debe conservar exactamente los bytes originales
                assert copied_apk.read_bytes() == original_apk.read_bytes()

                # El source_path en local_sources debe apuntar al directorio _attachments
                assert len(local_sources) >= 1
                assert local_sources[0]["source_path"] == str(attachments_dir.resolve())
                assert local_sources[0]["workspace_subdir"] == "test"

    def test_prepare_scan_targets_replaces_existing_file(self):
        """El parche siempre reemplaza: unlink() + copy2(). Confirma bytes nuevos."""
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            original_apk = tmp_path / "test.apk"
            original_apk.write_bytes(b"original content")

            test_runs_dir = tmp_path / "strix_runs"
            test_runs_dir.mkdir()

            with patch("strix_telegram_bot.config.settings") as mock_settings:
                mock_settings.strix_runs_dir = test_runs_dir

                # Primera ejecución
                targets = [str(original_apk)]
                bot._prepare_scan_targets(targets)

                attachments_dir = test_runs_dir / "repos" / "_attachments" / "test"
                copied_apk = attachments_dir / "test.apk"
                assert copied_apk.exists()
                assert copied_apk.read_bytes() == b"original content"

                # Modificar la APK original
                original_apk.write_bytes(b"new content")

                # Segunda ejecución — debe reemplazar, no ignorar
                bot._prepare_scan_targets(targets)

                # Los bytes finales deben corresponder a la APK nueva
                assert copied_apk.read_bytes() == b"new content"

    def test_prepare_scan_targets_replaces_symlink(self):
        """Si ya existe un symlink, lo reemplaza por un archivo regular."""
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            original_apk = tmp_path / "test.apk"
            original_apk.write_bytes(b"new content")

            test_runs_dir = tmp_path / "strix_runs"
            test_runs_dir.mkdir()

            with patch("strix_telegram_bot.config.settings") as mock_settings:
                mock_settings.strix_runs_dir = test_runs_dir

                # Crear un symlink manualmente en la ruta real
                attachments_dir = test_runs_dir / "repos" / "_attachments" / "test"
                attachments_dir.mkdir(parents=True, exist_ok=True)
                target_path = attachments_dir / "test.apk"
                target_path.symlink_to(original_apk)

                assert target_path.is_symlink()

                # Ejecutar _prepare_scan_targets
                targets = [str(original_apk)]
                bot._prepare_scan_targets(targets)

                # Debe haber reemplazado el symlink por un archivo regular
                assert target_path.exists()
                assert target_path.is_file()
                assert not target_path.is_symlink()
                assert target_path.read_bytes() == original_apk.read_bytes()


class TestPrepareScanTargetsDelegatesCloneToOfficial:
    """FASE 2: the bot no longer pre-clones GitHub repos. The URL is passed
    through uncloned; the official build_targets_info + prepare_run own the
    classification and the cloning."""

    def test_github_url_passes_through_uncloned(self):
        from strix_telegram_bot.bot import StrixBot
        bot = StrixBot()
        url = "https://github.com/facebook/zstd"
        prepared_targets, local_sources = bot._prepare_scan_targets([url])
        # The URL is passed through as-is (not converted to a local path)
        assert prepared_targets == [url]
        # No local_sources for a repository target (official collect_local_sources
        # builds them from cloned_repo_path after prepare_run clones it)
        assert local_sources == []

    def test_github_url_no_local_path_created(self, tmp_path, monkeypatch):
        """No local clone dir is created by the bot for a GitHub URL target."""
        from strix_telegram_bot.bot import StrixBot
        bot = StrixBot()

        test_runs_dir = tmp_path / "strix_runs"
        test_runs_dir.mkdir()
        with patch("strix_telegram_bot.config.settings") as mock_settings:
            mock_settings.strix_runs_dir = test_runs_dir
            prepared_targets, _ = bot._prepare_scan_targets(
                ["https://github.com/facebook/zstd"])
        # URL passed through, no local path substituted
        assert prepared_targets == ["https://github.com/facebook/zstd"]
        # No clone dir created under repos/
        repos_dir = test_runs_dir / "repos"
        if repos_dir.exists():
            assert not (repos_dir / "facebook" / "zstd").exists()
