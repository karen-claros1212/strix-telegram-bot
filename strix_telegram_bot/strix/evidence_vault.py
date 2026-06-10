from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.safety.redaction import redact_text


_MANIFEST_NAME = "manifest.json"
_CHUNK_SIZE = 65536


class EvidenceVault:
    def __init__(self, run_name: str) -> None:
        self.run_name = run_name
        self._vault_dir: Optional[Path] = None

    def _resolve_vault(self) -> Optional[Path]:
        for candidate in [
            settings.strix_runs_dir / self.run_name / "evidence",
            Path.cwd() / "strix_runs" / self.run_name / "evidence",
        ]:
            if candidate.exists() or candidate.parent.exists():
                return candidate
        return None

    def _ensure_vault(self) -> Path:
        if self._vault_dir is None:
            self._vault_dir = self._resolve_vault()
        if self._vault_dir is None:
            self._vault_dir = settings.strix_runs_dir / self.run_name / "evidence"
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        return self._vault_dir

    @staticmethod
    def hash_file(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def store_evidence(
        self,
        source_path: Path,
        subdir: str = "raw",
        sensitive: bool = False,
    ) -> Optional[dict]:
        vault = self._ensure_vault()
        target_dir = vault / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        target_path = target_dir / source_path.name
        if target_path.exists():
            base = target_path.stem
            target_path = target_dir / f"{base}_{int(time.time())}{target_path.suffix}"

        import shutil
        try:
            shutil.copy2(source_path, target_path)
        except (OSError, shutil.Error):
            return None

        sha256 = self.hash_file(target_path)
        stat = target_path.stat()

        artifact = {
            "id": f"{subdir}/{target_path.name}",
            "type": _classify_evidence(subdir, target_path.suffix),
            "path": str(target_path.relative_to(vault.parent)),
            "absolute_path": str(target_path.resolve()),
            "sha256": sha256,
            "size_bytes": stat.st_size,
            "sensitive": sensitive,
            "created_at": time.time(),
        }
        self._append_to_manifest(artifact)
        return artifact

    def store_bytes(
        self,
        data: bytes,
        file_name: str,
        subdir: str = "raw",
        sensitive: bool = False,
    ) -> Optional[dict]:
        vault = self._ensure_vault()
        target_dir = vault / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name

        if target_path.exists():
            base = target_path.stem
            target_path = target_dir / f"{base}_{int(time.time())}{target_path.suffix}"

        target_path.write_bytes(data)
        sha256 = self.hash_file(target_path)

        artifact = {
            "id": f"{subdir}/{target_path.name}",
            "type": _classify_evidence(subdir, target_path.suffix),
            "path": str(target_path.relative_to(vault.parent)),
            "absolute_path": str(target_path.resolve()),
            "sha256": sha256,
            "size_bytes": len(data),
            "sensitive": sensitive,
            "created_at": time.time(),
        }
        self._append_to_manifest(artifact)
        return artifact

    def get_manifest(self) -> dict:
        path = self._manifest_path()
        if path and path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"job_id": self.run_name, "artifacts": []}

    def _manifest_path(self) -> Optional[Path]:
        vault = self._vault_dir or self._resolve_vault()
        if vault is None:
            vault = settings.strix_runs_dir / self.run_name / "evidence"
        return vault / _MANIFEST_NAME if vault else None

    def _append_to_manifest(self, artifact: dict) -> None:
        path = self._manifest_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = self.get_manifest()
        manifest.setdefault("artifacts", [])
        manifest["artifacts"].append(artifact)
        try:
            path.write_text(json.dumps(manifest, indent=2, default=str))
        except OSError:
            pass

    def redacted_preview(
        self,
        artifact_id: str,
        max_chars: int = 1500,
    ) -> Optional[str]:
        manifest = self.get_manifest()
        artifact = None
        for a in manifest.get("artifacts", []):
            if a["id"] == artifact_id:
                artifact = a
                break
        if artifact is None:
            return None

        vault = self._vault_dir or self._resolve_vault()
        if vault is None:
            return None

        full_path = vault / artifact["id"]
        if not full_path.exists():
            return None

        content = full_path.read_text(encoding="utf-8", errors="replace")
        redacted = redact_text(content)
        if len(redacted) > max_chars:
            redacted = redacted[:max_chars] + "\n\n... (redacted preview truncated)"
        return redacted

    def list_evidence(self) -> list[dict]:
        manifest = self.get_manifest()
        return manifest.get("artifacts", [])

    def count_evidence(self) -> int:
        return len(self.list_evidence())

    def summary(self) -> str:
        artifacts = self.list_evidence()
        if not artifacts:
            return "No evidence stored yet."

        total_size = sum(a.get("size_bytes", 0) for a in artifacts)
        sensitive_count = sum(1 for a in artifacts if a.get("sensitive"))

        return (
            f"Evidence Vault: {self.run_name}\n"
            f"Artifacts: {len(artifacts)}\n"
            f"Total size: {total_size / 1024:.1f} KB\n"
            f"Sensitive: {sensitive_count}"
        )

    @staticmethod
    def get_vault_for(run_name: str) -> EvidenceVault:
        return EvidenceVault(run_name)


def _classify_evidence(subdir: str, ext: str) -> str:
    if subdir == "screenshots":
        return "screenshot"
    if subdir == "requests":
        return "request"
    if subdir == "responses":
        return "response"
    if subdir == "caido":
        return "caido"
    if subdir == "files":
        return "file"
    if subdir == "terminal":
        return "terminal"
    if subdir == "notes":
        return "note"
    ext_map = {
        ".txt": "text",
        ".md": "report",
        ".csv": "csv",
        ".json": "json",
        ".html": "html",
        ".png": "screenshot",
        ".jpg": "screenshot",
        ".jpeg": "screenshot",
        ".gif": "screenshot",
        ".pcap": "capture",
        ".har": "capture",
    }
    return ext_map.get(ext, "raw")
