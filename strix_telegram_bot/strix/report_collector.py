from __future__ import annotations

from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings


class ReportCollector:
    def __init__(self, run_name: str) -> None:
        self.run_name = run_name
        self._run_dir: Optional[Path] = None
        self._collected: list[dict] = []

    def _resolve_dir(self) -> Optional[Path]:
        for candidate in [
            settings.strix_runs_dir / self.run_name,
            Path.cwd() / "strix_runs" / self.run_name,
        ]:
            if candidate.exists():
                return candidate
        return None

    def collect(self) -> list[dict]:
        if self._run_dir is None:
            self._run_dir = self._resolve_dir()
            if self._run_dir is None:
                return []

        found: list[dict] = []
        for ext in ("*.md", "*.csv", "*.json", "*.html", "*.txt"):
            for fpath in self._run_dir.glob(ext):
                if fpath.is_file() and fpath.stat().st_size > 0:
                    found.append({
                        "name": fpath.name,
                        "path": str(fpath),
                        "size": fpath.stat().st_size,
                        "ext": fpath.suffix.lower(),
                    })

        self._collected = found
        return found

    def get_report_content(self, report_name: str, max_chars: int = 4000) -> Optional[str]:
        if self._run_dir is None:
            self._run_dir = self._resolve_dir()
            if self._run_dir is None:
                return None

        fpath = self._run_dir / report_name
        if not fpath.exists() or not fpath.is_file():
            return None

        content = fpath.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (truncated)"
        return content

    def summary(self) -> str:
        reports = self.collect()
        if not reports:
            return "No reports available yet."

        lines = [f"Reports for {self.run_name}:"]
        for r in reports:
            size_kb = r["size"] / 1024
            lines.append(f"  {r['name']} ({size_kb:.1f} KB)")
        return "\n".join(lines)
