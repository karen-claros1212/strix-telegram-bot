from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from strix_telegram_bot.config import settings
from strix_telegram_bot.safety.redaction import redact_text
from strix_telegram_bot.strix.evidence_vault import EvidenceVault


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
        reports_dir = self._run_dir / "reports"
        if reports_dir.exists():
            search_dir = reports_dir
        else:
            search_dir = self._run_dir

        for ext in ("*.md", "*.csv", "*.json", "*.html", "*.txt"):
            for fpath in search_dir.glob(ext):
                if fpath.is_file() and fpath.stat().st_size > 0:
                    found.append({
                        "name": fpath.name,
                        "path": str(fpath),
                        "size": fpath.stat().st_size,
                        "ext": fpath.suffix.lower(),
                    })

        self._collected = found
        return found

    def get_report_content(
        self, report_name: str, max_chars: int = 4000
    ) -> Optional[str]:
        if self._run_dir is None:
            self._run_dir = self._resolve_dir()
            if self._run_dir is None:
                return None

        reports_dir = self._run_dir / "reports"
        for base in (reports_dir, self._run_dir):
            fpath = base / report_name
            if fpath.exists() and fpath.is_file():
                content = fpath.read_text(encoding="utf-8", errors="replace")
                if len(content) > max_chars:
                    content = content[:max_chars] + "\n\n... (truncated)"
                return content
        return None

    def get_latest_report(self) -> Optional[dict]:
        reports = self.collect()
        if not reports:
            return None
        reports.sort(key=lambda r: r.get("size", 0), reverse=True)
        return reports[0]

    def get_markdown_report(self) -> Optional[str]:
        return self.get_report_content("penetration_test_report.md")

    def get_csv_report(self) -> Optional[str]:
        return self.get_report_content("vulnerabilities.csv")

    def get_json_events(self) -> Optional[list[dict]]:
        if self._run_dir is None:
            self._run_dir = self._resolve_dir()
            if self._run_dir is None:
                return None
        events_path = self._run_dir / "events.jsonl"
        if not events_path.exists():
            return None
        events = []
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    def build_executive_summary(self) -> Optional[str]:
        reports = self.collect()
        if not reports:
            return None

        target_redacted = self.run_name.replace("strix_runs/", "")
        lines = [
            "Executive Summary: STRIX",
            f"Run: {target_redacted}",
            f"Reports: {len(reports)}",
            "",
            "Available reports:",
        ]
        for r in reports:
            size_kb = r["size"] / 1024
            lines.append(f"  {r['name']} ({size_kb:.1f} KB)")

        md = self.get_markdown_report()
        if md:
            for line in md.split("\n")[:15]:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    lines.append(f"  {stripped[:80]}")

        return "\n".join(lines)

    def collect_artifacts(self) -> list[dict]:
        if self._run_dir is None:
            self._run_dir = self._resolve_dir()
            if self._run_dir is None:
                return []

        artifacts = []
        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.pcap", "*.har", "*.zip", "*.log"):
            for fpath in self._run_dir.rglob(pattern):
                if fpath.is_file():
                    artifacts.append({
                        "name": fpath.name,
                        "path": str(fpath),
                        "size": fpath.stat().st_size,
                    })
        return artifacts

    def summary(self) -> str:
        reports = self.collect()
        if not reports:
            return "No reports available yet."

        vault = EvidenceVault(self.run_name)
        ev_count = vault.count_evidence()

        lines = [f"Reports for {self.run_name}:"]
        for r in reports:
            size_kb = r["size"] / 1024
            lines.append(f"  {r['name']} ({size_kb:.1f} KB)")
        if ev_count:
            lines.append(f"Evidence artifacts: {ev_count}")
        return "\n".join(lines)

    @staticmethod
    def list_jobs_with_reports(limit: int = 10) -> list[dict]:
        runs_dir = settings.strix_runs_dir
        if not runs_dir.exists():
            return []
        jobs = []
        for entry in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not entry.is_dir():
                continue
            reports_dir_candidates = [entry / "reports", entry]
            has_reports = False
            report_count = 0
            for rd in reports_dir_candidates:
                if rd.exists():
                    for f in rd.iterdir():
                        if f.is_file() and f.suffix in (".md", ".csv", ".json", ".html", ".txt"):
                            has_reports = True
                            report_count += 1
            if has_reports:
                jobs.append({
                    "run_name": entry.name,
                    "report_count": report_count,
                    "path": str(entry),
                })
                if len(jobs) >= limit:
                    break
        return jobs
