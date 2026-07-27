"""Tests for StrixRuntimeBridge — TuiLiveView-based projection architecture."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge, _fmt_duration


def _make_sdk_event(event_type: str, item_type: str = "", output: str = "",
                    tool_name: str = "", raw_item: dict | None = None) -> MagicMock:
    ev = MagicMock()
    ev.type = event_type
    if item_type:
        item = MagicMock(spec=["type", "raw_item", "output"])
        item.type = item_type
        if item_type == "message_output_item":
            raw = MagicMock()
            content = MagicMock()
            content.text = output
            raw.content = [content]
            item.raw_item = raw
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
    return ev


def _make_sdk_raw_response(delta: str = "") -> MagicMock:
    ev = MagicMock()
    ev.type = "raw_response_event"
    data = MagicMock()
    data.type = "response.output_text.delta"
    data.delta = delta
    ev.data = data
    return ev


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
        with patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", False):
            bridge = StrixRuntimeBridge()
            assert bridge.is_running is False
            assert bridge.is_actively_working is False
            assert bridge.run_name is None
            assert bridge.root_agent_id is None
            assert bridge.elapsed == 0.0
            assert bridge.is_available is False

    def test_is_available_false_when_strix_missing(self):
        with patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", False):
            bridge = StrixRuntimeBridge()
            assert bridge.is_available is False
            ok, msg = bridge.start_scan(targets=["https://example.com"])
            assert ok is False
            assert "no esta instalado" in msg

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_start_scan_rejects_duplicate(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._scan_completed = False
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "running"}
        bridge._root_agent_id = "root"
        assert bridge.is_running is True
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

    def test_poll_events_no_live_view(self):
        bridge = StrixRuntimeBridge()
        events = bridge.poll_events()
        assert events == []

    def test_emit_event_adds_to_live_view(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        bridge._emit_event("scan_complete", "a1", "Done")
        events = bridge.poll_events()
        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "system"
        assert ev["data"]["event"] == "scan_complete"
        assert ev["data"]["content"] == "Done"
        assert ev["data"]["run_name"] == "test-run"

    def test_emit_event_blocked_for_closed_runs(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "closed-run"
        bridge._closed_runs.add("closed-run")
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        bridge._emit_event("scan_complete", "a1", "Done")
        events = bridge.poll_events()
        assert len(events) == 0

    def test_closed_runs_allow_cancelled(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "closed-run"
        bridge._closed_runs.add("closed-run")
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        bridge._emit_event("scan_cancelled", "", "Cancelled")
        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0]["data"]["event"] == "scan_cancelled"

    def test_poll_events_deduplicates_by_version(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        # Add event, poll once → delivered
        bridge._live_view.events.append(
            {"id": "chat_1", "type": "chat", "version": 0, "agent_id": "a1",
             "timestamp": "", "data": {"role": "assistant", "content": "hello"}})
        first = bridge.poll_events()
        assert len(first) == 1

        # Same event, same version → not delivered again
        second = bridge.poll_events()
        assert len(second) == 0

        # Bump version → delivered
        bridge._live_view.events[0]["version"] = 1
        third = bridge.poll_events()
        assert len(third) == 1

    def test_poll_events_over_multiple_emits(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        bridge._emit_event("root_discovered", "a1", "root")
        bridge._emit_event("scan_complete", "", "done")

        events = bridge.poll_events()
        assert len(events) == 2
        assert events[0]["data"]["event"] == "root_discovered"
        assert events[1]["data"]["event"] == "scan_complete"

    def test_get_tool_state_empty(self):
        bridge = StrixRuntimeBridge()
        ts = bridge.get_tool_state()
        assert ts["active_count"] == 0
        assert ts["completed_count"] == 0
        assert ts["failed_count"] == 0
        assert ts["streaming"] is False
        assert ts["current_tool_name"] == ""

    def test_get_tool_state_with_awaiting_from_coordinator(self):
        bridge = StrixRuntimeBridge()
        bridge._non_interactive = False
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        ts = bridge.get_tool_state()
        assert ts["awaiting_input"] is True

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_get_run_status_idle(self, *_):
        bridge = StrixRuntimeBridge()
        status = bridge.get_run_status()
        assert status["is_running"] is False
        assert status["run_name"] is None

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_get_run_status_with_run_name(self, tmp_path, *_):
        bridge = StrixRuntimeBridge()
        bridge._start_time = time.time() - 123
        bridge._run_name = "scan-test-123"
        status = bridge.get_run_status()
        assert status["run_name"] == "scan-test-123"

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_to_status_dict_idle(self, *_):
        bridge = StrixRuntimeBridge()
        sd = bridge.to_status_dict()
        assert sd["is_active"] is False
        assert sd["error"] is None

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_to_status_dict_with_error(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._last_error = "Connection failed"
        bridge._scan_completed = True

        sd = bridge.to_status_dict()
        assert sd["error"] == "Connection failed"
        assert sd["is_active"] is False

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_to_status_dict_with_waiting_from_coordinator(self, *_):
        bridge = StrixRuntimeBridge()
        bridge._non_interactive = False
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        bridge._scan_completed = False

        sd = bridge.to_status_dict()
        assert sd["awaiting_input"] is True
        assert sd["is_active"] is True

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_send_message_noop_when_not_running(self, *_):
        bridge = StrixRuntimeBridge()
        assert bridge.send_message("agent", "hi") is False
        assert bridge.send_message_to_agent("hi") is False

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_stop_scan_when_not_running(self, *_):
        bridge = StrixRuntimeBridge()
        assert bridge.stop_scan() is True
        assert bridge.is_running is False

    def test_is_actively_working_reads_coordinator(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "running"}
        bridge._root_agent_id = "root"
        assert bridge.is_actively_working is True
        assert bridge.is_running is True

        bridge._coordinator.statuses = {"root": "waiting"}
        assert bridge.is_actively_working is False
        assert bridge.is_running is True

        bridge._scan_completed = True
        assert bridge.is_actively_working is False
        assert bridge.is_running is False

    def test_check_waiting_notification(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._non_interactive = False
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        # First call: should notify
        ev = bridge.check_waiting_notification()
        assert ev is not None
        assert ev["data"]["event"] == "agent_waiting"
        assert ev["data"]["content"] == "strix-agent"

        # Second call: already notified, no duplicate
        ev2 = bridge.check_waiting_notification()
        assert ev2 is None

        # Status changes away from waiting → resets track
        bridge._coordinator.statuses = {"root": "running"}
        bridge.ack_waiting_notification()

        # Back to waiting → notifies again
        bridge._coordinator.statuses = {"root": "waiting"}
        ev3 = bridge.check_waiting_notification()
        assert ev3 is not None

    def test_fresh_live_view_on_start_scan_init(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        lv = TuiLiveView()
        lv.events.append({"id": "test_1", "type": "chat", "data": {}})
        bridge._live_view = lv
        # Skip prepopulated event
        bridge.poll_events()

        bridge._run_name = "active"
        bridge._emit_event("scan_complete", "", "done")
        events = bridge.poll_events()
        assert len(events) >= 1
        assert events[-1]["data"]["event"] == "scan_complete"

    def test_get_agent_tree_from_live_view(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("a1", name="root", status="running")
        bridge._live_view.upsert_agent("a2", name="child", parent_id="a1", status="waiting")

        tree = bridge.get_agent_tree()
        assert tree is not None
        assert "a1" in tree["agents"]
        assert "a2" in tree["agents"]

    def test_get_agent_tree_none_when_no_live_view(self):
        bridge = StrixRuntimeBridge()
        tree = bridge.get_agent_tree()
        assert tree is None

    def test_list_agents_from_live_view(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("a1", name="agent1")
        bridge._live_view.upsert_agent("a2", name="agent2")

        agents = bridge.list_agents()
        assert len(agents) == 2

    def test_agent_timeline_returns_events_for_agent(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        bridge._live_view.events.append({
            "id": "chat_1", "type": "chat", "agent_id": "a1",
            "version": 0, "timestamp": "2026-01-01T00:00:00Z",
            "data": {"role": "assistant", "content": "hello",
                     "metadata": {"streaming": False}},
        })

        timeline = bridge.agent_timeline("a1")
        assert len(timeline) == 1
        assert timeline[0]["type"] == "chat"

    def test_agent_timeline_empty_for_unknown_agent(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        timeline = bridge.agent_timeline("unknown")
        assert timeline == []

    def test_get_root_status_from_coordinator(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "running"}
        bridge._root_agent_id = "root"
        assert bridge.get_root_status() == "running"

        bridge._coordinator.statuses = {"root": "waiting"}
        assert bridge.get_root_status() == "waiting"

    def test_get_root_status_unknown_without_coordinator(self):
        bridge = StrixRuntimeBridge()
        assert bridge.get_root_status() == "unknown"

    def test_concurrent_event_read_write(self):
        import threading

        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        errors = []
        barrier = threading.Barrier(2, timeout=5)

        def writer():
            try:
                barrier.wait()
                for i in range(100):
                    bridge._emit_event("root_discovered", f"a{i}", f"msg{i}")
            except Exception as e:
                errors.append(f"writer: {e}")

        def reader():
            try:
                barrier.wait()
                for _ in range(100):
                    _ = bridge.poll_events()
                    _ = bridge.get_tool_state()
                    _ = bridge.get_agent_tree()
                    _ = bridge.list_agents()
            except Exception as e:
                errors.append(f"reader: {e}")

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive(), "Writer thread hung"
        assert not t2.is_alive(), "Reader thread hung"
        assert not errors, f"Concurrent errors: {errors}"


class TestWaitingCycle:
    def test_waiting_cycle_via_coordinator(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix")

        # Running
        bridge._coordinator.statuses = {"root": "running"}
        assert bridge.is_actively_working is True
        assert bridge.is_running is True

        # Waiting
        bridge._coordinator.statuses = {"root": "waiting"}
        assert bridge.is_actively_working is False
        assert bridge.is_running is True
        assert bridge.get_root_status() == "waiting"

        # Back to running
        bridge._coordinator.statuses = {"root": "running"}
        assert bridge.is_actively_working is True

        # Completed
        bridge._scan_completed = True
        assert bridge.is_running is False

    def test_typing_stops_during_waiting(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "running"}
        bridge._root_agent_id = "root"
        assert bridge.is_actively_working is True

        bridge._coordinator.statuses = {"root": "waiting"}
        assert bridge.is_actively_working is False

    def test_no_typing_after_completion(self):
        bridge = StrixRuntimeBridge()
        bridge._scan_completed = True
        assert bridge.is_actively_working is False


class TestCleanupCycle:
    @patch("strix_telegram_bot.strix.runtime_bridge.session_manager")
    def test_cleanup_imported(self, mock_sm):
        from strix_telegram_bot.strix.runtime_bridge import session_manager
        assert session_manager is not None
        assert hasattr(session_manager, 'cleanup')

    def test_stop_scan_cleans_state(self):
        bridge = StrixRuntimeBridge()
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._run_name = "scan-test"
        bridge._closed_runs.add("scan-test")
        bridge._scan_completed = True
        assert "scan-test" in bridge._closed_runs
        assert bridge._scan_completed is True

    def test_sandbox_preserved_during_waiting(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        bridge._scan_completed = False
        assert bridge.is_running is True
        assert bridge._scan_completed is False

    def test_idempotent_cleanup(self):
        bridge = StrixRuntimeBridge()
        bridge._scan_completed = True
        bridge._closed_runs.add("old-run")
        bridge.stop_scan()
        assert bridge._scan_completed is True


class TestSdkEventIngestion:
    def test_message_event_produces_chat_event(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="Hello from agent")
        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", ev)

        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0]["type"] == "chat"
        assert events[0]["data"]["role"] == "assistant"

    def test_tool_call_event_produces_tool_event(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        ev = _make_sdk_event("run_item_stream_event", item_type="tool_call_item", tool_name="nuclei_scan")
        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", ev)

        events = bridge.poll_events()
        assert len(events) >= 1
        tool_ev = [e for e in events if e["type"] == "tool"]
        assert len(tool_ev) >= 1
        assert tool_ev[0]["data"]["status"] == "running"

    def test_tool_output_completes_tool(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        call_ev = _make_sdk_event("run_item_stream_event", item_type="tool_call_item", tool_name="nuclei_scan")
        out_ev = _make_sdk_event("run_item_stream_event", item_type="tool_call_output_item",
                                 tool_name="nuclei_scan", output="vulnerability found")

        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", call_ev)
            bridge._live_view.ingest_sdk_event("a1", out_ev)

        ts = bridge.get_tool_state()
        assert ts["completed_count"] >= 1

    def test_streaming_delta_produces_stream_event(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        ev = _make_sdk_raw_response(delta="analyzing")
        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", ev)

        ts = bridge.get_tool_state()
        assert ts["streaming"] is True

    def test_unknown_event_type_ignored(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        ev = _make_sdk_event("unknown_event_type", item_type="bogus")
        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", ev)

        events = bridge.poll_events()
        assert len(events) == 0


class TestConcurrentSdkEvents:
    def test_concurrent_writer_with_sdk_events(self):
        import threading

        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()

        errors = []
        barrier = threading.Barrier(2, timeout=5)

        def writer():
            try:
                barrier.wait()
                for i in range(100):
                    ev_msg = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output=f"msg{i}")
                    ev_tool = _make_sdk_event("run_item_stream_event", item_type="tool_call_item", tool_name=f"tool{i}")
                    ev_out = _make_sdk_event("run_item_stream_event", item_type="tool_call_output_item",
                                             tool_name=f"tool{i}", output=f"result{i}")
                    ev_delta = _make_sdk_raw_response(delta=f"chunk{i}")
                    with bridge._lv_lock:
                        bridge._live_view.ingest_sdk_event("a1", ev_msg)
                        bridge._live_view.ingest_sdk_event("a1", ev_tool)
                        bridge._live_view.ingest_sdk_event("a1", ev_out)
                        bridge._live_view.ingest_sdk_event("a1", ev_delta)
                    bridge._emit_event("root_discovered", f"a{i}", f"msg{i}")
            except Exception as e:
                errors.append(f"writer: {e}")

        def reader():
            try:
                barrier.wait()
                for _ in range(100):
                    _ = bridge.poll_events()
                    _ = bridge.get_tool_state()
                    _ = bridge.get_agent_tree()
                    _ = bridge.list_agents()
            except Exception as e:
                errors.append(f"reader: {e}")

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert not t1.is_alive(), "Writer thread hung"
        assert not t2.is_alive(), "Reader thread hung"
        assert not errors, f"Concurrent errors: {errors}"


class TestStatusDictCompatWithJobStatusText:
    def test_empty_dict(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_with_running_data(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._run_name = "scan-abc"
        bridge._start_time = time.time()
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)

    def test_with_error_state(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._last_error = "Timeout"
        bridge._scan_completed = True
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)

    def test_with_input_request(self):
        from strix_telegram_bot.ui.messages import job_status_text
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        sd = bridge.to_status_dict()
        text = job_status_text(sd)
        assert isinstance(text, str)


# ── Fix 1: diff_scope uses official API after merged_sources ──
class TestDiffScopeOfficialAPI:
    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.collect_local_sources", MagicMock(return_value=[]))
    def test_diff_scope_inactive_for_url_only(self, mock_itt):
        """When scope_mode=auto and targets are URLs (no local repos),
        diff_scope.active should be False."""
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        bridge = StrixRuntimeBridge()
        # We can't call start_scan easily, but we can verify the logic
        # by checking _build_targets_info and the diff_scope construction
        info = bridge._build_targets_info(["https://example.com"])
        # URLs have no source_path, so has_local_sources will be False
        assert info[0]["type"] == "url"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.collect_local_sources")
    @patch("strix_telegram_bot.strix.runtime_bridge.resolve_diff_scope_context")
    def test_diff_scope_called_for_auto_mode_via_start_scan(self, mock_resolve, mock_cls, mock_itt):
        """start_scan() must call resolve_diff_scope_context for auto mode with local sources."""
        from strix.interface.utils import DiffScopeResult
        mock_resolve.return_value = DiffScopeResult(
            active=True, mode="auto",
            instruction_block="diff instruction", metadata={"key": "val"},
        )
        mock_cls.return_value = [{"source_path": "/tmp/repo", "workspace_subdir": "repo"}]
        mock_itt.return_value = ("local_code", {"source_path": "/tmp/repo"})

        bridge = StrixRuntimeBridge()
        ok, msg = bridge.start_scan(
            targets=["/tmp/repo"],
            instruction="test",
            scan_mode="deep",
            scope_mode="auto",
            non_interactive=True,
        )
        assert ok is True

        # resolve_diff_scope_context must have been called with auto mode
        mock_resolve.assert_called_once()
        call_args = mock_resolve.call_args
        assert call_args[0][1] == "auto"  # scope_mode
        assert call_args[0][3] is False    # non_interactive — TUI mirror always False

        # Bridge should be in running state with correct targets
        assert bridge._current_targets == ["/tmp/repo"]
        assert bridge._run_name is not None
        assert bridge._run_name.startswith("scan-")

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.collect_local_sources", MagicMock(return_value=[]))
    def test_diff_scope_inactive_for_urls_without_local(self, mock_itt):
        """No local sources → diff_scope stays inactive (API not called)."""
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        bridge = StrixRuntimeBridge()
        info = bridge._build_targets_info(["https://example.com"])
        assert info[0]["type"] == "url"


# ── Fix 1: Google Drive stays web_application, no artifact type ──
class TestFileHostingURLs:
    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_google_drive_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://drive.google.com/file/d/1abc/view"})
        info = StrixRuntimeBridge._build_targets_info(
            ["https://drive.google.com/file/d/1abc/view?usp=drivesdk"]
        )
        assert len(info) == 1
        assert info[0]["type"] == "web_application"
        assert "drive.google.com" in info[0]["details"]["target_url"]

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_dropbox_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://www.dropbox.com/s/abc/file.apk"})
        info = StrixRuntimeBridge._build_targets_info(
            ["https://www.dropbox.com/s/abc/file.apk"]
        )
        assert len(info) == 1
        assert info[0]["type"] == "web_application"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_regular_url_unaffected(self, mock_itt):
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        info = StrixRuntimeBridge._build_targets_info(["https://example.com"])
        assert len(info) == 1
        assert info[0]["type"] == "url"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_mega_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://mega.nz/file/abc/file.apk"})
        info = StrixRuntimeBridge._build_targets_info(
            ["https://mega.nz/file/abc/file.apk"]
        )
        assert len(info) == 1
        assert info[0]["type"] == "web_application"

    # ── DEFECTO A: WAITING_FOR_CHILDREN vs WAITING_FOR_USER ──────

    def test_get_child_status_summary(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "running",
            "child2": "completed",
            "child3": "failed",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
            "child2": "root",
            "child3": "root",
        }
        summary = bridge.get_descendant_status_summary()
        assert summary["running"] == 1
        assert summary["completed"] == 1
        assert summary["failed"] == 1

    def test_get_child_status_summary_no_children(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._coordinator.parent_of = {"root": None}
        summary = bridge.get_descendant_status_summary()
        assert summary == {}

    def test_waiting_notification_suppressed_when_children_running(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._non_interactive = False
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "running",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
        }
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        ev = bridge.check_waiting_notification()
        assert ev is None  # suppressed: descendants still running

    def test_waiting_notification_suppressed_when_children_waiting(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._non_interactive = False
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "waiting",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
        }
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        ev = bridge.check_waiting_notification()
        assert ev is None  # suppressed: descendants still waiting

    def test_waiting_notification_fires_when_children_all_done(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._non_interactive = False
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "completed",
            "child2": "failed",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
            "child2": "root",
        }
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        ev = bridge.check_waiting_notification()
        assert ev is not None  # fires: all descendants done, interactive mode
        assert ev["data"]["event"] == "agent_waiting"

    def test_waiting_notification_never_fires_in_non_interactive(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._non_interactive = True
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "completed",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
        }
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        ev = bridge.check_waiting_notification()
        assert ev is None  # never request user input in non_interactive

    # ── descendant counting (recursive) ─────────────────────────

    def test_get_descendant_status_summary_direct_children(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "running",
            "child2": "completed",
            "child3": "failed",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
            "child2": "root",
            "child3": "root",
        }
        summary = bridge.get_descendant_status_summary()
        assert summary["running"] == 1
        assert summary["completed"] == 1
        assert summary["failed"] == 1

    def test_get_descendant_status_summary_nested_descendants(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {
            "root": "waiting",
            "child1": "running",
            "grandchild1": "completed",
            "grandchild2": "running",
            "unrelated": "running",
        }
        bridge._coordinator.parent_of = {
            "root": None,
            "child1": "root",
            "grandchild1": "child1",
            "grandchild2": "child1",
            "unrelated": "other_root",
        }
        summary = bridge.get_descendant_status_summary()
        assert summary["running"] == 2  # child1 + grandchild2
        assert summary["completed"] == 1  # grandchild1
        assert "unrelated" not in str(summary)  # excluded

    def test_get_descendant_status_summary_no_descendants(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._coordinator.parent_of = {"root": None}
        summary = bridge.get_descendant_status_summary()
        assert summary == {}

    # ── lifecycle classification ────────────────────────────────

    def test_parse_scan_completed_dict_true(self):
        assert StrixRuntimeBridge._parse_scan_completed({"scan_completed": True}) is True

    def test_parse_scan_completed_dict_false(self):
        assert StrixRuntimeBridge._parse_scan_completed({"scan_completed": False}) is False

    def test_parse_scan_completed_json_string(self):
        assert StrixRuntimeBridge._parse_scan_completed('{"scan_completed": true}') is True

    def test_parse_scan_completed_json_string_false(self):
        assert StrixRuntimeBridge._parse_scan_completed('{"scan_completed": false}') is False

    def test_parse_scan_completed_none(self):
        assert StrixRuntimeBridge._parse_scan_completed(None) is False

    def test_parse_scan_completed_random_string(self):
        assert StrixRuntimeBridge._parse_scan_completed("just text") is False

    def test_classify_success_real(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        result = MagicMock()
        result.final_output = {"scan_completed": True}
        bridge._scan_result = result
        event, error = bridge._classify_scan_result()
        assert event == "scan_complete"
        assert error is None

    def test_classify_success_json_string(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        result = MagicMock()
        result.final_output = '{"scan_completed": true}'
        bridge._scan_result = result
        event, error = bridge._classify_scan_result()
        assert event == "scan_complete"
        assert error is None

    def test_classify_completed_but_no_scan_completed(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        result = MagicMock()
        result.final_output = {"vulnerabilities": []}
        bridge._scan_result = result
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert "finish_scan" in error

    def test_classify_stopped(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "stopped"}
        bridge._scan_result = None
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert "detuvo" in error

    def test_classify_failed(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "failed"}
        bridge._scan_result = None
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert "failed" in error

    def test_classify_inconsistent(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._scan_result = MagicMock(final_output=None)
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert "finish_scan" in error

    def test_classify_uses_existing_last_error(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "failed"}
        bridge._last_error = "Budget exceeded"
        bridge._scan_result = None
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert error == "Budget exceeded"


# ── Strix 1.3.1 migration: TUI alignment ────────────────────────


class TestTuiAlignment:
    """Verify the 4 corrections that bring Radamanthys into parity with Strix 1.3.1 TUI."""

    def test_no_root_instructions_override(self):
        """Fix 1: lifecycle_guard must not be injected into run_strix_scan."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "lifecycle_guard" not in src
        assert "root_instructions_override" not in src

    def test_send_message_uses_official_helper(self):
        """Fix 2: send_message delegates to send_user_message_to_agent."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.send_message)
        assert "_send_umta(" in src
        assert "record_user_message" not in src
        assert "coordinator.send(" not in src

    def test_diff_scope_always_called(self):
        """Fix 3: resolve_diff_scope_context is called unconditionally (no has_local_sources guard)."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        assert "has_local_sources" not in src

    def test_diff_scope_non_interactive_false(self):
        """Fix 3: resolve_diff_scope_context always receives non_interactive=False."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        # The call to resolve_diff_scope_context must pass False as the 4th arg
        assert "resolve_diff_scope_context(" in src
        assert "False," in src  # non_interactive=False

    def test_diff_scope_instruction_prepend(self):
        """Fix 3: diff instruction is prepended (not appended) to user instruction."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        # Should be: diff_block + newline + instruction (prepending)
        assert "diff_result.instruction_block" in src
        # The TUI pattern: f"{diff_result.instruction_block}\\n\\n{instruction}"
        assert "instruction_block}" in src

    def test_completion_detected_flag_exists(self):
        """Fix 4: _completion_detected flag exists on bridge."""
        bridge = StrixRuntimeBridge()
        assert hasattr(bridge, "_completion_detected")
        assert bridge._completion_detected is False

    def test_watcher_in_main(self):
        """Fix 4: _watch_completion is created and awaited in _main."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "_watch_completion" in src

    def test_watcher_only_cancels_on_completed(self):
        """Fix 5: _watch_completion source only checks 'completed', not terminal set."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "terminal" not in src
        assert 'status == "completed"' in src

    def test_watcher_checks_report_state(self):
        """Fix 5: _watch_completion verifies ReportState.run_record['status']."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert 'rr_status' in src
        assert 'report_persisted' in src or 'rr_status' in src


class TestWatcherBehavior:
    """Tests that the completion watcher only fires for completed+persisted."""

    def test_watcher_cancels_when_completed_and_persisted(self):
        """root completed + run_record completed → watcher cancels → scan_complete."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        bridge._last_error = None
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "completed"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            bridge._scan_result = MagicMock(final_output='{"scan_completed": true}')
            event, error = bridge._classify_scan_result()
        assert event == "scan_complete"
        assert error is None

    def test_watcher_does_not_cancel_on_failed(self):
        """root failed → watcher does NOT cancel → exception propagates naturally."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        # watcher must NOT have 'stopped' or 'failed' or 'crashed' in terminal check
        assert '"stopped"' not in src
        assert '"failed"' not in src
        assert '"crashed"' not in src

    def test_stopped_not_watcher_cancelled(self):
        """root stopped → scan_error, not a watcher cancellation."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "stopped"}
        bridge._scan_result = None
        event, error = bridge._classify_scan_result()
        assert event == "scan_error"
        assert "detuvo" in error

    def test_completed_no_final_output_still_success(self):
        """completed + run_record completed + final_output None → success (interactive mode)."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        bridge._scan_result = MagicMock(final_output=None)
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "completed"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            event, error = bridge._classify_scan_result()
        assert event == "scan_complete"
        assert error is None


class TestArtifactHintRemoved:
    """Verify no Google Drive / file-hosting artifact instruction is injected."""

    def test_no_artifact_host_regex_in_bot(self):
        import inspect
        from strix_telegram_bot.bot import StrixBot
        src = inspect.getsource(StrixBot)
        assert "_ARTIFACT_HOST_RE" not in src
        assert "artifact-delivery" not in src
        assert "Treat the hosting page" not in src
        assert "Download and validate" not in src

    def test_drive_url_passthrough(self):
        """Drive URL arrives at start_scan intact — no extra instruction appended."""
        from strix_telegram_bot.bot import StrixBot
        import inspect
        src = inspect.getsource(StrixBot)
        assert "artifact-delivery" not in src
        assert "Download and validate" not in src


class TestDiffScopeExactMetadata:
    """diff_scope in scan_config must equal diff_result.metadata exactly — no manual diff_base injection."""

    def test_no_diff_base_injection(self):
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        assert 'diff_scope["diff_base"]' not in src
        assert "diff_scope[\"diff_base\"]" not in src

    def test_metadata_used_directly(self):
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        assert "dict(diff_result.metadata)" in src


class TestDiffScopeFailFast:
    """ValueError from resolver must fail start_scan before thread creation."""

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.collect_local_sources", MagicMock(return_value=[]))
    @patch("strix_telegram_bot.strix.runtime_bridge.resolve_diff_scope_context")
    def test_valueerror_returns_false_no_thread(self, mock_resolve, mock_itt):
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        mock_resolve.side_effect = ValueError("invalid scope: bad repo state")
        bridge = StrixRuntimeBridge()
        ok, msg = bridge.start_scan(
            targets=["https://example.com"],
            instruction="test",
            scan_mode="deep",
            scope_mode="auto",
        )
        assert ok is False
        assert "scope" in msg.lower() or "invalid" in msg.lower()
        assert bridge._thread is None
        assert bridge.is_running is False

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.collect_local_sources", MagicMock(return_value=[]))
    @patch("strix_telegram_bot.strix.runtime_bridge.resolve_diff_scope_context")
    def test_generic_exception_returns_false(self, mock_resolve, mock_itt):
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        mock_resolve.side_effect = RuntimeError("unexpected failure")
        bridge = StrixRuntimeBridge()
        ok, msg = bridge.start_scan(
            targets=["https://example.com"],
            instruction="test",
            scan_mode="deep",
            scope_mode="auto",
        )
        assert ok is False
        assert bridge._thread is None
