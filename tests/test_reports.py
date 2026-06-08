"""Test Report Center — collector, summary, history."""

from __future__ import annotations

from pathlib import Path

from strix_telegram_bot.strix.report_collector import ReportCollector


class TestReportCollector:
    def test_nonexistent_run(self):
        rc = ReportCollector("nonexistent-run-12345")
        reports = rc.collect()
        assert reports == []

    def test_summary_empty(self):
        rc = ReportCollector("nonexistent")
        assert "No reports" in rc.summary()

    def test_collect_reports(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "penetration_test_report.md").write_text("# Report")
        (run_dir / "vulnerabilities.csv").write_text("id,severity")

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            rc = ReportCollector("test-run")
            reports = rc.collect()
            assert len(reports) == 2

            content = rc.get_markdown_report()
            assert content is not None
            assert "# Report" in content

            csv = rc.get_csv_report()
            assert csv is not None
            assert "id,severity" in csv
        finally:
            settings.strix_runs_dir = old_dir

    def test_collect_reports_in_reports_subdir(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "summary.md").write_text("# Summary")

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            rc = ReportCollector("test-run")
            reports = rc.collect()
            assert len(reports) >= 1
        finally:
            settings.strix_runs_dir = old_dir

    def test_get_report_content(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# Test Report\n\nContent here.")

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            rc = ReportCollector("test-run")
            content = rc.get_report_content("report.md")
            assert content is not None
            assert "Test Report" in content

            assert rc.get_report_content("nonexistent.md") is None
        finally:
            settings.strix_runs_dir = old_dir

    def test_build_executive_summary(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# Report\n\nFinding: XSS vulnerability")

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            rc = ReportCollector("test-run")
            summary = rc.build_executive_summary()
            assert summary is not None
            assert "Executive Summary" in summary
            assert "test-run" in summary
        finally:
            settings.strix_runs_dir = old_dir

    def test_get_json_events(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        run_dir.mkdir(parents=True)
        events_file = run_dir / "events.jsonl"
        events_file.write_text('{"event_type":"run.started"}\n{"event_type":"run.completed"}\n')

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            rc = ReportCollector("test-run")
            events = rc.get_json_events()
            assert events is not None
            assert len(events) == 2
            assert events[0]["event_type"] == "run.started"
        finally:
            settings.strix_runs_dir = old_dir

    def test_list_jobs_with_reports(self, tmp_path):
        run_dir = tmp_path / "strix_runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# Report")

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            jobs = ReportCollector.list_jobs_with_reports(limit=5)
            assert len(jobs) >= 1
            assert jobs[0]["run_name"] == "test-run"
            assert jobs[0]["report_count"] >= 1
        finally:
            settings.strix_runs_dir = old_dir
