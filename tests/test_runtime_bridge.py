"""Tests for StrixRuntimeBridge — mocks STRIX imports."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from strix_telegram_bot.models import JobPhase, ScanMode
from strix_telegram_bot.strix.runtime_bridge import ScanEvent, StrixRuntimeBridge, _fmt_duration


class TestScanEvent:
    def test_create_defaults(self):
        ev = ScanEvent()
        assert ev.type == ""
        assert ev.agent_id == ""
        assert ev.content == ""
        assert ev.timestamp == 0.0
        assert ev.awaiting_input is False

    def test_create_with_values(self):
        ev = ScanEvent(type="agent.message", agent_id="abc123", content="hello",
                       timestamp=1000.0, awaiting_input=True, prompt="answer?")
        assert ev.type == "agent.message"
        assert ev.agent_id == "abc123"
        assert ev.content == "hello"
        assert ev.timestamp == 1000.0
        assert ev.awaiting_input is True
        assert ev.prompt == "answer?"

    def test_to_dict(self):
        ev = ScanEvent(type="test", agent_id="a1", content="x", timestamp=1.0)
        d = ev.to_dict()
        assert d["type"] == "test"
        assert d["agent_id"] == "a1"
        assert d["content"] == "x"
        assert d["timestamp"] == 1.0


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(5) == "5s"
        assert _fmt_duration(59) == "59s"

    def test_minutes(self):
        assert _fmt_duration(60) == "1m 00s"
        assert _fmt_duration(125) == "2m 05s"

    def test_hours(self):
        assert _fmt_duration(3600) == "1h 00m 00s"
        assert _fmt_duration(3665) == "1h 01m 05s"


class TestStrixRuntimeBridge:
    def test_initial_state(self):
        bridge = StrixRuntimeBridge()
        assert bridge.is_running is False
        assert bridge.run_name is None
        assert bridge.root_agent_id is None
        assert bridge.elapsed == 0.0
        assert bridge.is_available is False  # strix not installed in test env

    def test_is_available_false_when_strix_missing(self):
        bridge = StrixRuntimeBridge()
        assert bridge.is_available is False
        ok, msg = bridge.start_scan(targets=["https://example.com"])
        assert ok is False
        assert "no está instalado" in msg

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_start_scan_rejects_duplicate(self, *_):
        class FakeAlive:
            is_alive = lambda self: True
            daemon = False

        bridge = StrixRuntimeBridge()
        bridge._thread = FakeAlive()

        ok, msg = bridge.start_scan(targets=["https://example.com"])
        assert ok is False
        assert "Ya hay" in msg

    def test_build_targets_info_url(self):
        info = StrixRuntimeBridge._build_targets_info(["https://example.com", "http://test.local"])
        assert len(info) == 2
        assert info[0] == {"type": "url", "value": "https://example.com"}
        assert info[1] == {"type": "url", "value": "http://test.local"}

    def test_build_targets_info_domain_adds_scheme(self):
        info = StrixRuntimeBridge._build_targets_info(["example.com", "test.org/path"])
        assert len(info) == 2
        assert info[0] == {"type": "url", "value": "https://example.com"}
        assert info[1] == {"type": "url", "value": "https://test.org/path"}

    def test_build_targets_info_local_path(self):
        info = StrixRuntimeBridge._build_targets_info(["/home/user/project", "~/repo", "./src"])
        assert len(info) == 3
        assert info[0]["type"] == "local"
        assert info[1]["type"] == "local"
        assert info[2]["type"] == "local"

    def test_build_targets_info_strips_whitespace(self):
        info = StrixRuntimeBridge._build_targets_info(["  https://example.com  ", ""])
        assert len(info) == 1

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_detects_root_agent(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("agent_xyz", {"type": "agent.created", "agent_id": "agent_xyz"})
        assert bridge.root_agent_id == "agent_xyz"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_detects_run_name(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("a1", {"type": "run.started", "run_name": "scan-abc123"})
        assert bridge.run_name == "scan-abc123"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_queues_events(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("a1", {"type": "agent.message", "content": "hello"})
        bridge._capture_event("a2", {"type": "input_request", "prompt": "What target?", "content": ""})

        events = bridge.poll_events()
        assert len(events) == 2
        assert events[0].type == "agent.message"
        assert events[0].content == "hello"
        assert events[1].type == "input_request"
        assert events[1].prompt == "What target?"
        assert events[1].awaiting_input is True

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_max_queue_size(self, *_):
        bridge = StrixRuntimeBridge()
        for i in range(600):
            bridge._capture_event("a", {"type": "event", "content": str(i)})
        assert bridge._event_queue.qsize() <= 500

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_emit_scan_events_via_capture(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._emit_event("scan_complete", "", "Done")
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "scan_complete"
        assert events[0].content == "Done"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_poll_events_drains_queue(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("a", {"type": "e1"})
        bridge._capture_event("a", {"type": "e2"})

        assert bridge._event_queue.qsize() == 2

        first = bridge.poll_events()
        assert len(first) == 2

        second = bridge.poll_events()
        assert len(second) == 0

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_get_run_status_idle(self, *_):
        bridge = StrixRuntimeBridge()
        status = bridge.get_run_status()
        assert status["is_running"] is False
        assert status["run_name"] is None
        assert status["phase"] == "running"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_get_run_status_with_run_name(self, tmp_path, *_):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-test-123"
        bridge._start_time = time.time() - 123

        run_dir = tmp_path / "scan-test-123"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps({
            "scan_mode": "deep",
            "status": "scanning",
        }))

        with patch.object(bridge, "_run_name", "scan-test-123"):
            status = bridge.get_run_status()
            assert status["is_running"] is False
            assert status["run_name"] == "scan-test-123"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_to_status_dict_idle(self, *_):
        bridge = StrixRuntimeBridge()
        sd = bridge.to_status_dict()
        assert sd["is_active"] is False
        assert sd["phase"] == "completed"
        assert sd["awaiting_input"] is False
        assert sd["error"] is None

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_to_status_dict_from_events(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("a1", {"type": "input_request", "prompt": "Enter URL:"})
        bridge._capture_event("a1", {"type": "agent.message", "content": "Scanning..."})
        bridge._capture_event("a1", {"type": "scan_error", "content": "Connection failed"})

        sd = bridge.to_status_dict()
        assert sd["awaiting_input"] is True
        assert sd["input_prompt"] == "Enter URL:"
        assert sd["phase"] == "failed"
        assert sd["error"] == "Connection failed"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_send_message_noop_when_not_running(self, *_):
        bridge = StrixRuntimeBridge()
        assert bridge.send_message("agent", "hi") is False
        assert bridge.send_message_to_agent("hi") is False

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_stop_scan_noop_when_not_running(self, *_):
        bridge = StrixRuntimeBridge()
        assert bridge.stop_scan() is False


class TestStatusDictCompatWithJobStatusText:
    """Verify to_status_dict output works with job_status_text()."""

    from strix_telegram_bot.ui.messages import job_status_text

    def test_empty_dict(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
        assert len(text) > 0
        assert "?" not in text  # no "unknown" markers

    def test_with_running_data(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-abc"
        bridge._start_time = time.time()
        sd = bridge.to_status_dict()

        with patch.object(bridge, "_run_name", "scan-abc"):
            text = job_status_text(sd)
            assert isinstance(text, str)

    def test_with_error_state(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._emit_event("scan_error", "", "Timeout")
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
        assert "Error" in text or "error" in text

    def test_with_input_request(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._capture_event("a1", {"type": "input_request", "prompt": "Answer?"})
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
