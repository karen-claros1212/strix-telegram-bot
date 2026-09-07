"""Test _prepare_scan_targets — official workspace_files for uploads, local_code
dirs as targets, URLs passed through uncloned (no bot-side staging/clone)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from strix_telegram_bot.bot import StrixBot


class TestPrepareScanTargetsWorkspaceFiles:
    """Uploaded files go through the official workspace_files mechanism
    (read_workspace_files -> extra_files) instead of a bot-private wrap dir."""

    def test_attachment_becomes_workspace_file(self):
        """A single uploaded file is routed to workspace_files (no copy, no target)."""
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            original_apk = Path(tmp) / "test.apk"
            original_apk.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 16)

            targets = [str(original_apk)]
            prepared_targets, local_sources, workspace_files = (
                bot._prepare_scan_targets(targets)
            )

            # The file is NOT a scan target and NOT a local source.
            assert prepared_targets == []
            assert local_sources == []

            # It IS a workspace file pointing at the original (no copy).
            assert len(workspace_files) == 1
            wf = workspace_files[0]
            assert wf["source_path"] == str(original_apk.resolve())
            assert wf["workspace_path"] == "test.apk"

    def test_no_wrap_dir_created_for_attachment(self):
        """No bot-private repos/_attachments dir is created for an upload."""
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            original_apk = tmp_path / "test.apk"
            original_apk.write_bytes(b"payload")

            test_runs_dir = tmp_path / "strix_runs"
            test_runs_dir.mkdir()

            with patch("strix_telegram_bot.config.settings") as mock_settings:
                mock_settings.strix_runs_dir = test_runs_dir
                bot._prepare_scan_targets([str(original_apk)])

            # The old staging location must not have been created.
            attachments_dir = test_runs_dir / "repos" / "_attachments"
            assert not attachments_dir.exists()


class TestPrepareScanTargetsLocalDir:
    """A local directory is a code target (mounted by the official flow)."""

    def test_local_dir_becomes_target_and_source(self):
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            code_dir = Path(tmp) / "mycode"
            code_dir.mkdir()
            (code_dir / "main.py").write_text("print('hi')")

            prepared_targets, local_sources, workspace_files = (
                bot._prepare_scan_targets([str(code_dir)])
            )

            assert prepared_targets == [str(code_dir.resolve())]
            assert workspace_files == []
            assert len(local_sources) == 1
            assert local_sources[0]["source_path"] == str(code_dir.resolve())
            assert local_sources[0]["workspace_subdir"] == "mycode"


class TestPrepareScanTargetsDelegatesCloneToOfficial:
    """The bot no longer pre-clones GitHub repos. The URL is passed through
    uncloned; the official build_targets_info + prepare_run own the
    classification and the cloning."""

    def test_github_url_passes_through_uncloned(self):
        bot = StrixBot()
        url = "https://github.com/facebook/zstd"
        prepared_targets, local_sources, workspace_files = (
            bot._prepare_scan_targets([url])
        )
        # The URL is passed through as-is (not converted to a local path).
        assert prepared_targets == [url]
        # No local_sources / workspace_files for a repository target.
        assert local_sources == []
        assert workspace_files == []

    def test_github_url_no_local_path_created(self, tmp_path):
        """No local clone dir is created by the bot for a GitHub URL target."""
        bot = StrixBot()

        test_runs_dir = tmp_path / "strix_runs"
        test_runs_dir.mkdir()
        with patch("strix_telegram_bot.config.settings") as mock_settings:
            mock_settings.strix_runs_dir = test_runs_dir
            prepared_targets, _, _ = bot._prepare_scan_targets(
                ["https://github.com/facebook/zstd"])
        # URL passed through, no local path substituted.
        assert prepared_targets == ["https://github.com/facebook/zstd"]
        # No clone dir created under repos/.
        repos_dir = test_runs_dir / "repos"
        if repos_dir.exists():
            assert not (repos_dir / "facebook" / "zstd").exists()


class TestPrepareScanTargetsMixed:
    """A mix of a local dir (target) and an upload (workspace file)."""

    def test_mixed_targets_and_workspace_files(self):
        bot = StrixBot()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            code_dir = tmp_path / "src"
            code_dir.mkdir()
            (code_dir / "app.py").write_text("x = 1")
            upload = tmp_path / "notes.txt"
            upload.write_bytes(b"hello")

            prepared_targets, local_sources, workspace_files = (
                bot._prepare_scan_targets([str(code_dir), str(upload)])
            )

            assert prepared_targets == [str(code_dir.resolve())]
            assert len(local_sources) == 1
            assert local_sources[0]["source_path"] == str(code_dir.resolve())
            assert len(workspace_files) == 1
            assert workspace_files[0]["source_path"] == str(upload.resolve())
            assert workspace_files[0]["workspace_path"] == "notes.txt"
