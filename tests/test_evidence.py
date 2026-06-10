"""Test Evidence Vault and evidence management."""

from __future__ import annotations

import time
from pathlib import Path

from strix_telegram_bot.strix.evidence_vault import EvidenceVault, _classify_evidence


class TestEvidenceVault:
    def test_store_and_manifest(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        vault._vault_dir.mkdir(parents=True)

        f = tmp_path / "test.txt"
        f.write_text("hello world")

        artifact = vault.store_evidence(f, subdir="raw")
        assert artifact is not None
        assert artifact["sha256"] is not None
        assert artifact["size_bytes"] == 11
        assert "absolute_path" in artifact
        assert Path(artifact["absolute_path"]).exists()

        manifest = vault.get_manifest()
        assert len(manifest["artifacts"]) == 1
        assert manifest["artifacts"][0]["sha256"] == artifact["sha256"]

    def test_store_bytes(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        vault._vault_dir.mkdir(parents=True)

        artifact = vault.store_bytes(b"binary data", "data.bin", subdir="raw")
        assert artifact is not None
        assert artifact["size_bytes"] == 11
        assert "absolute_path" in artifact
        assert Path(artifact["absolute_path"]).exists()

    def test_hash_file(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"x" * 100000)

        h1 = EvidenceVault.hash_file(f)
        h2 = EvidenceVault.hash_file(f)
        assert h1 == h2
        assert len(h1) == 64

    def test_list_evidence(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        vault._vault_dir.mkdir(parents=True)

        f = tmp_path / "a.txt"
        f.write_text("a")
        vault.store_evidence(f)

        f2 = tmp_path / "b.txt"
        f2.write_text("b")
        vault.store_evidence(f2, sensitive=True)

        items = vault.list_evidence()
        assert len(items) == 2

    def test_redacted_preview(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        vault._vault_dir.mkdir(parents=True)

        f = tmp_path / "config.txt"
        f.write_text("api_key = sk-1234567890123456789012345678901234567890")
        artifact = vault.store_evidence(f)

        preview = vault.redacted_preview(artifact["id"])
        assert preview is not None
        assert "sk-" not in preview
        assert "***" in preview

    def test_summary_empty(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        assert "No evidence" in vault.summary()

    def test_count_evidence(self, tmp_path):
        vault = EvidenceVault("test-run")
        vault._vault_dir = tmp_path / "evidence"
        vault._vault_dir.mkdir(parents=True)
        assert vault.count_evidence() == 0

        f = tmp_path / "x.txt"
        f.write_text("test")
        vault.store_evidence(f)
        assert vault.count_evidence() == 1


class TestClassifyEvidence:
    def test_classify_screenshot(self):
        assert _classify_evidence("screenshots", ".png") == "screenshot"

    def test_classify_request(self):
        assert _classify_evidence("requests", ".txt") == "request"

    def test_classify_caido(self):
        assert _classify_evidence("caido", ".json") == "caido"

    def test_classify_by_ext(self):
        assert _classify_evidence("raw", ".csv") == "csv"
        assert _classify_evidence("raw", ".md") == "report"
        assert _classify_evidence("raw", ".pcap") == "capture"
        assert _classify_evidence("raw", ".bin") == "raw"
