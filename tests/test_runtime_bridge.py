"""Tests for StrixRuntimeBridge — mocks STRIX imports."""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from strix_telegram_bot.models import JobPhase, ScanMode
from strix_telegram_bot.strix.runtime_bridge import ScanEvent, StrixRuntimeBridge, _fmt_duration, _MAX_EVENTS


def _make_sdk_event(event_type: str, item_type: str = "", output: str = "",
                    tool_name: str = "", raw_item: dict | None = None) -> MagicMock:
    """Build a mock SDK event object matching strix SDK event structure."""
    ev = MagicMock()
    ev.type = event_type
    if item_type:
        item = MagicMock(spec=["type", "raw_item", "output", "raw_response_event"])
        item.type = item_type
        if item_type == "message_output_item":
            raw = MagicMock()
            content = MagicMock()
            content.text = output
            raw.content = [content]
            item.raw_item = raw
            item.raw_response_event = "dummy"
        elif item_type == "tool_call_item":
            raw = MagicMock()
            raw.name = tool_name or "test_tool"
            raw.arguments = json.dumps({"arg1": "val1"})
            raw.call_id = "call_1"
            item.raw_item = raw
        elif item_type == "tool_call_output_item":
            item.output = output
            raw = MagicMock()
            raw.name = tool_name or "test_tool"
            raw.output = output
            raw.call_id = "call_1"
            item.raw_item = raw
        ev.item = item
    if raw_item is not None:
        ev.raw_item = raw_item
    return ev


def _make_sdk_raw_response(delta: str = "") -> MagicMock:
    ev = MagicMock()
    ev.type = "raw_response_event"
    data = MagicMock()
    data.type = "response.output_text.delta"
    data.delta = delta
    ev.data = data
    return ev


class TestScanEvent:
    def test_create_defaults(self):
        ev = ScanEvent()
        assert ev.type == ""
        assert ev.agent_id == ""
        assert ev.content == ""
        assert ev.timestamp == 0.0
        assert ev.awaiting_input is False

    def test_create_with_values(self):
        ev = ScanEvent(type="agent_message", agent_id="abc123", content="hello",
                       timestamp=1000.0, awaiting_input=True, prompt="answer?")
        assert ev.type == "agent_message"
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
        assert bridge.is_available is False
        assert bridge.scan_status == "unknown"

    def test_is_available_false_when_strix_missing(self):
        bridge = StrixRuntimeBridge()
        assert bridge.is_available is False
        ok, msg = bridge.start_scan(targets=["https://example.com"])
        assert ok is False
        assert "no está instalado" in msg

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_start_scan_rejects_duplicate(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._scan_status = "running"

        ok, msg = bridge.start_scan(targets=["https://example.com"])
        assert ok is False
        assert "Ya hay" in msg

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_build_targets_info_url(self, mock_itt):
        mock_itt.side_effect = lambda t: ("url", {"target_url": t})

        info = StrixRuntimeBridge._build_targets_info(
            ["https://example.com", "http://test.local"]
        )
        assert len(info) == 2
        assert info[0]["type"] == "url"
        assert info[1]["type"] == "url"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_build_targets_info_domain_adds_scheme(self, mock_itt):
        mock_itt.side_effect = lambda t: ("web_application", {"target_url": f"https://{t}"})

        info = StrixRuntimeBridge._build_targets_info(["example.com", "test.org/path"])
        assert len(info) == 2

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_build_targets_info_strips_whitespace(self, mock_itt):
        mock_itt.side_effect = lambda t: ("url", {"target_url": t.strip()})

        info = StrixRuntimeBridge._build_targets_info(["  https://example.com  ", ""])
        assert len(info) == 1

    def test_capture_event_ignores_unknown(self):
        bridge = StrixRuntimeBridge()
        bridge._capture_event("test-run", "a1", {"type": "unknown_event"})
        assert bridge._event_queue.qsize() == 0

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_raw_response_stores_delta(self, *_):
        bridge = StrixRuntimeBridge()
        ev = _make_sdk_raw_response(delta="hello")
        bridge._capture_event("test-run", "a1", ev)
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "stream_delta"
        assert events[0].content == "hello"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_queues_message_event(self, *_):
        bridge = StrixRuntimeBridge()
        ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="Hello agent")
        bridge._capture_event("test-run", "a1", ev)

        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "agent_message"
        assert events[0].content == "Hello agent"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_queues_tool_call(self, *_):
        bridge = StrixRuntimeBridge()
        ev = _make_sdk_event("run_item_stream_event", item_type="tool_call_item", tool_name="scan")
        bridge._capture_event("test-run", "a1", ev)

        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "tool_call"
        assert "scan" in events[0].content

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_queues_tool_output(self, *_):
        bridge = StrixRuntimeBridge()
        ev = _make_sdk_event("run_item_stream_event", item_type="tool_call_output_item",
                             tool_name="scan", output="results")
        bridge._capture_event("test-run", "a1", ev)

        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "tool_output"
        assert "results" in events[0].content

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_capture_event_max_queue_size(self, *_):
        bridge = StrixRuntimeBridge()
        for i in range(600):
            ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output=str(i))
            bridge._capture_event("test-run", "a", ev)
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
        ev1 = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="msg1")
        ev2 = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="msg2")
        bridge._capture_event("test-run", "a", ev1)
        bridge._capture_event("test-run", "a", ev2)

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
        bridge._start_time = time.time() - 123

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
    def test_to_status_dict_with_phase_and_error(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._phase = "failed"
        bridge._last_error = "Connection failed"
        bridge._scan_completed = True

        sd = bridge.to_status_dict()
        assert sd["phase"] == "failed"
        assert sd["error"] == "Connection failed"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_to_status_dict_with_waiting(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._awaiting_input = True
        bridge._input_prompt = "Enter URL:"

        sd = bridge.to_status_dict()
        assert sd["awaiting_input"] is True
        assert sd["input_prompt"] == "Enter URL:"

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
    def test_stop_scan_when_not_running(self, *_):
        bridge = StrixRuntimeBridge()
        assert bridge.stop_scan() is True
        assert bridge.is_running is False

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_normalize_output_string(self, *_):
        assert StrixRuntimeBridge._normalize_output("hello") == "hello"


class TestEventIsolation:
    """Verify that events are tagged with run_name and stale events are filtered."""

    def test_scan_event_has_run_name(self):
        ev = ScanEvent(type="agent_message", agent_id="a1", content="hello", run_name="scan-abc123")
        assert ev.run_name == "scan-abc123"
        d = ev.to_dict()
        assert "run_name" in d
        assert d["run_name"] == "scan-abc123"

    def test_scan_event_run_name_defaults_empty(self):
        ev = ScanEvent(type="agent_message", agent_id="a1", content="hello")
        assert ev.run_name == ""

    def test_emit_event_tags_with_run_name(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run-999"
        bridge._emit_event("scan_complete", "", "Done")
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].run_name == "test-run-999"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_closed_runs_block_events(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-old"
        bridge._closed_runs.add("scan-old")
        ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="stale message")
        bridge._capture_event("scan-old", "a1", ev)
        assert bridge._event_queue.qsize() == 0

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_closed_runs_allow_cancelled(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-old"
        bridge._closed_runs.add("scan-old")
        bridge._emit_event("scan_cancelled", "", "Cancelled")
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].type == "scan_cancelled"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_active_runs_accept_events(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-current"
        ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="active message")
        bridge._capture_event("scan-current", "a1", ev)
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0].run_name == "scan-current"

    def test_fresh_queue_on_start(self):
        bridge = StrixRuntimeBridge()
        # Simulate leftover events
        bridge._event_queue.put(ScanEvent(type="agent_message", run_name="old", content="stale"))
        assert bridge._event_queue.qsize() == 1
        # Fresh queue replaces old one
        bridge._event_queue = queue.Queue(maxsize=_MAX_EVENTS)
        assert bridge._event_queue.qsize() == 0

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_normalize_output_dict(self, *_):
        result = StrixRuntimeBridge._normalize_output({"key": "value"})
        assert "key" in result
        assert "value" in result

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    @patch("strix_telegram_bot.strix.runtime_bridge.ReportState", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.set_global_report_state", MagicMock())
    def test_normalize_output_number(self, *_):
        result = StrixRuntimeBridge._normalize_output(42)
        assert "42" in result


class TestStatusDictCompatWithJobStatusText:
    """Verify to_status_dict output works with job_status_text()."""

    def test_empty_dict(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
        assert len(text) > 0
        assert "?" not in text

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
        bridge._phase = "failed"
        bridge._last_error = "Timeout"
        bridge._scan_completed = True
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
        assert "Error" in text or "error" in text or "Timeout" in text

    def test_with_input_request(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._awaiting_input = True
        bridge._input_prompt = "Answer?"
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
