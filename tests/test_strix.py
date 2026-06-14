"""Test STRIX adapter layer — CLI, events, reports, caido."""

from __future__ import annotations

import pytest
from strix_telegram_bot.strix.caido_panel import CaidoPanel
from strix_telegram_bot.strix.report_collector import ReportCollector


class TestCaidoPanel:
    def test_initial_state(self):
        cp = CaidoPanel()
        assert cp.url is None
        assert cp.active is False

    def test_update_from_text(self):
        cp = CaidoPanel()
        url = cp.update_from_text("Caido proxy at http://caido.local:8080")
        assert url is not None
        assert cp.active is True

    def test_clear(self):
        cp = CaidoPanel()
        cp.set_url("http://caido.local:8080")
        cp.clear()
        assert cp.url is None
        assert cp.active is False

class TestReportCollector:
    def test_nonexistent_run(self):
        rc = ReportCollector("nonexistent-run-12345")
        reports = rc.collect()
        assert reports == []

    def test_summary_empty(self):
        rc = ReportCollector("nonexistent")
        assert "No reports" in rc.summary()
