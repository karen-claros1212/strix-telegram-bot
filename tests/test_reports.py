"""Test Report Center — collector, summary, history."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from strix_telegram_bot.strix.report_collector import ReportCollector
from strix_telegram_bot.models import JobPhase, JobState, ScanMode


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


# ── Fix 4+6: "Último reporte" validates run.json + filters COMPLETED ──
class TestSendLatestReportFilter:
    def test_send_latest_report_skips_failed_jobs(self, monkeypatch):
        """_send_latest_report should only pick JobPhase.COMPLETED, not FAILED/STOPPED."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        failed_job = JobState(
            run_name="scan-failed", target=["x"], mode=ScanMode.DEEP,
            phase=JobPhase.FAILED,
        )
        completed_job = JobState(
            run_name="scan-done", target=["x"], mode=ScanMode.DEEP,
            phase=JobPhase.COMPLETED,
        )

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [failed_job, completed_job]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        fake_bot = MagicMock()
        mock_rc = MagicMock()
        mock_rc.get_full_markdown_report.return_value = "# Report Content"
        monkeypatch.setattr(reports_mod, "ReportCollector", lambda run: mock_rc)

        with patch.object(reports_mod, "_send_fragmented", return_value=True) as mock_frag, \
             patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            mock_frag.assert_called_once()
            call_text = mock_frag.call_args[0][2]
            assert "scan-done" in call_text

    def test_send_latest_report_skips_stopped_jobs(self, monkeypatch):
        """STOPPED jobs should not be picked by 'Último reporte'."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        stopped_job = JobState(
            run_name="scan-stopped", target=["x"], mode=ScanMode.DEEP,
            phase=JobPhase.STOPPED,
        )

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [stopped_job]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            sent_text = mock_edit.call_args[0][3]
            assert "No hay trabajos completados" in sent_text

    def test_send_latest_report_empty_when_no_completed(self, monkeypatch):
        """When all jobs are FAILED/STOPPED, should show 'No hay trabajos completados'."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        failed1 = JobState(run_name="s1", target=["x"], phase=JobPhase.FAILED)
        failed2 = JobState(run_name="s2", target=["x"], phase=JobPhase.STOPPED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [failed1, failed2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            sent_text = mock_edit.call_args[0][3]
            assert "No hay trabajos completados" in sent_text

    def test_send_latest_report_fallback_to_next_job_with_content(self, monkeypatch):
        """Should loop through jobs — first has empty report, second has content."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job1 = JobState(run_name="scan-empty", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)
        job2 = JobState(run_name="scan-good", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job1, job2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        def fake_rc(run_name):
            mock = MagicMock()
            if run_name == "scan-empty":
                mock.get_full_markdown_report.return_value = ""
            else:
                mock.get_full_markdown_report.return_value = "# Good Report"
            return mock

        monkeypatch.setattr(reports_mod, "ReportCollector", fake_rc)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "_send_fragmented", return_value=True) as mock_frag, \
             patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            mock_frag.assert_called_once()
            call_text = mock_frag.call_args[0][2]
            assert "scan-good" in call_text

    def test_send_latest_report_all_empty_shows_no_valid(self, monkeypatch):
        """All completed jobs have empty reports → 'No se encontraron reportes válidos'."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job1 = JobState(run_name="scan-a", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)
        job2 = JobState(run_name="scan-b", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job1, job2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        mock_rc = MagicMock()
        mock_rc.get_full_markdown_report.return_value = ""
        monkeypatch.setattr(reports_mod, "ReportCollector", lambda run: mock_rc)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            sent_text = mock_edit.call_args[0][3]
            assert "No se encontraron reportes válidos" in sent_text

    def test_send_latest_report_skips_run_json_not_completed(self, monkeypatch):
        """Jobs with run.json status != completed should be skipped."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job1 = JobState(run_name="scan-partial", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)
        job2 = JobState(run_name="scan-done", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job1, job2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)

        def fake_run_json(run_name):
            return run_name == "scan-done"

        monkeypatch.setattr(reports_mod, "_is_run_json_completed", fake_run_json)

        def fake_rc(run_name):
            mock = MagicMock()
            mock.get_full_markdown_report.return_value = "# Report"
            return mock

        monkeypatch.setattr(reports_mod, "ReportCollector", fake_rc)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "_send_fragmented", return_value=True) as mock_frag, \
             patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_latest_report(fake_bot, 123, 456)
            mock_frag.assert_called_once()
            call_text = mock_frag.call_args[0][2]
            assert "scan-done" in call_text

    def test_send_executive_summary_fallback(self, monkeypatch):
        """Executive summary should also loop to find job with valid summary."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job1 = JobState(run_name="scan-no", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)
        job2 = JobState(run_name="scan-yes", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job1, job2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        def fake_rc(run_name):
            mock = MagicMock()
            if run_name == "scan-no":
                mock.build_executive_summary.return_value = ""
            else:
                mock.build_executive_summary.return_value = "# Summary"
            return mock

        monkeypatch.setattr(reports_mod, "ReportCollector", fake_rc)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_executive_summary(fake_bot, 123, 456)
            calls = mock_edit.call_args_list
            summary_calls = [c for c in calls if "# Summary" in (c[0][3] if len(c[0]) > 3 else "")]
            assert len(summary_calls) == 1

    def test_send_report_type_fallback(self, monkeypatch):
        """_send_report_type should loop and pick first with valid content."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job1 = JobState(run_name="scan-empty", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)
        job2 = JobState(run_name="scan-csv", target=["x"], mode=ScanMode.DEEP,
                        phase=JobPhase.COMPLETED)

        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job1, job2]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)

        def fake_rc(run_name):
            mock = MagicMock()
            if run_name == "scan-empty":
                mock.get_report_content.return_value = ""
            else:
                mock.get_report_content.return_value = "id,severity\n1,high"
            return mock

        monkeypatch.setattr(reports_mod, "ReportCollector", fake_rc)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "_send_fragmented", return_value=True) as mock_frag, \
             patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._send_report_type(fake_bot, 123, 456, "csv")
            mock_frag.assert_called_once()
            call_text = mock_frag.call_args[0][2]
            assert "CSV" in call_text
            assert "id,severity" in call_text


class TestDeliverReportDocument:
    def test_deliver_uses_selected_run_name(self, monkeypatch):
        """download_md must deliver the exact run_name passed in the callback."""
        from strix_telegram_bot.commands import reports as reports_mod

        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: run == "scan-picked")
        mock_deliver = MagicMock(return_value="delivered")
        monkeypatch.setattr(reports_mod, "deliver_report_document", mock_deliver)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._deliver_report_document(fake_bot, 123, 456, "scan-picked")
            mock_deliver.assert_called_once_with(fake_bot, 123, "scan-picked")
        delivered_calls = [c for c in mock_edit.call_args_list if "enviado como archivo" in c[0][3]]
        assert len(delivered_calls) == 1

    def test_deliver_rejects_unknown_run(self, monkeypatch):
        """A run_name that is not completed must be rejected without falling back."""
        from strix_telegram_bot.commands import reports as reports_mod

        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: False)
        mock_deliver = MagicMock()
        monkeypatch.setattr(reports_mod, "deliver_report_document", mock_deliver)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._deliver_report_document(fake_bot, 123, 456, "scan-unknown")
            mock_deliver.assert_not_called()
        sent_text = mock_edit.call_args[0][3]
        assert "no está disponible" in sent_text

    def test_deliver_falls_back_to_latest_when_no_run_name(self, monkeypatch):
        """Without a run_name, fall back to the latest completed job."""
        from strix_telegram_bot.commands import reports as reports_mod
        from strix_telegram_bot.jobs.job_store import JobStore

        job = JobState(run_name="scan-latest", target=["x"], mode=ScanMode.DEEP,
                       phase=JobPhase.COMPLETED)
        mock_store = MagicMock(spec=JobStore)
        mock_store.list_recent.return_value = [job]
        monkeypatch.setattr(reports_mod, "JobStore", lambda: mock_store)
        monkeypatch.setattr(reports_mod, "_is_run_json_completed", lambda run: True)
        mock_deliver = MagicMock(return_value="delivered")
        monkeypatch.setattr(reports_mod, "deliver_report_document", mock_deliver)

        fake_bot = MagicMock()
        with patch.object(reports_mod, "edit_message") as mock_edit:
            reports_mod._deliver_report_document(fake_bot, 123, 456)
            mock_deliver.assert_called_once_with(fake_bot, 123, "scan-latest")
        delivered_calls = [c for c in mock_edit.call_args_list if "enviado como archivo" in c[0][3]]
        assert len(delivered_calls) == 1
