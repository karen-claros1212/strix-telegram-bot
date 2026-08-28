"""Tests for StrixRuntimeBridge — TuiLiveView-based projection architecture."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock, PropertyMock, AsyncMock, patch

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

        loop = asyncio.new_event_loop()
        async def noop():
            await asyncio.sleep(100)
        bridge._scan_task = loop.create_task(noop())
        bridge._loop = loop
        try:
            assert bridge.is_running is True
            ok, msg = bridge.start_scan(targets=["https://example.com"])
            assert ok is False
            assert "Ya hay" in msg
        finally:
            bridge._scan_task.cancel()
            loop.run_until_complete(asyncio.gather(bridge._scan_task, return_exceptions=True))
            loop.close()

    def test_build_targets_info_url(self):
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://example.com", "http://test.local"], target_list=[])
        build_targets_info(args)
        assert len(args.targets_info) == 2
        assert args.targets_info[0]["type"] == "web_application"
        assert args.targets_info[1]["type"] == "web_application"

    def test_build_targets_info_domain_adds_scheme(self):
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["example.com", "test.org/path"], target_list=[])
        build_targets_info(args)
        assert len(args.targets_info) == 2

    def test_build_targets_info_strips_whitespace(self):
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["  https://example.com  "], target_list=[])
        build_targets_info(args)
        assert len(args.targets_info) == 1

    def test_poll_events_no_live_view(self):
        bridge = StrixRuntimeBridge()
        events = bridge.poll_events()
        assert events == []

    def test_poll_events_no_synthetic_lifecycle_events(self):
        bridge = StrixRuntimeBridge()

        bridge._scan_completed = True
        bridge._terminal_kind = "completed"

        events = bridge.poll_events()
        assert events == []

    def test_poll_events_returns_only_sdk_events(self):
        from types import SimpleNamespace
        bridge = StrixRuntimeBridge()

        bridge._scan_completed = True
        bridge._terminal_kind = "completed"
        bridge._live_view = SimpleNamespace(events=[])

        events = bridge.poll_events()
        assert events == []

    def test_poll_events_delivers_sdk_events_not_lifecycle(self):
        from types import SimpleNamespace
        bridge = StrixRuntimeBridge()

        sdk_event = {
            "id": "sdk_1", "type": "chat", "agent_id": "a1",
            "version": 0, "data": {"role": "assistant", "content": "hello"},
        }
        bridge._live_view = SimpleNamespace(events=[sdk_event])

        events = bridge.poll_events()
        assert len(events) == 1
        assert events[0]["id"] == "sdk_1"
        assert events[0]["type"] == "chat"

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

    def test_poll_events_over_multiple_sdk_events(self):
        from types import SimpleNamespace as SN
        bridge = StrixRuntimeBridge()
        bridge._live_view = SN(events=[])

        bridge._live_view.events.append(
            {"id": "sdk_1", "type": "chat", "agent_id": "a1",
             "version": 0, "data": {"role": "assistant", "content": "first"}})
        bridge._live_view.events.append(
            {"id": "sdk_2", "type": "chat", "agent_id": "a1",
             "version": 0, "data": {"role": "assistant", "content": "second"}})

        events = bridge.poll_events()
        assert len(events) == 2
        assert events[0]["id"] == "sdk_1"
        assert events[1]["id"] == "sdk_2"

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
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"

        loop = asyncio.new_event_loop()
        bridge._loop = loop

        async def mock_wait_kind(agent_id):
            return "user"
        bridge._coordinator.wait_kind_of = mock_wait_kind

        import threading as _threading
        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ts = bridge.get_tool_state()
            assert ts["awaiting_input"] is True
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

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
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        bridge._scan_completed = False

        loop = asyncio.new_event_loop()
        async def mock_wait_kind(agent_id):
            return "user"
        bridge._coordinator.wait_kind_of = mock_wait_kind

        async def noop():
            await asyncio.sleep(100)
        bridge._scan_task = loop.create_task(noop())
        bridge._loop = loop

        import threading as _threading
        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            sd = bridge.to_status_dict()
            assert sd["awaiting_input"] is True
            assert sd["is_active"] is True
        finally:
            bridge._scan_task.cancel()
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            loop.close()

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


    # ── FASE 5: deterministic stop ─────────────────────────────

    @patch("strix_telegram_bot.strix.runtime_bridge._STRIX_AVAILABLE", True)
    def test_start_scan_rejected_when_thread_alive(self, *_):
        """A live previous thread must block a new run (deterministic guard)."""
        import threading as _th

        bridge = StrixRuntimeBridge()
        # A live thread simulating a scan that is still finishing
        stop = _th.Event()

        def _worker():
            stop.wait(timeout=5)

        t = _th.Thread(target=_worker, daemon=True)
        t.start()
        bridge._thread = t
        try:
            ok, msg = bridge.start_scan(["https://github.com/foo/bar"])
            assert ok is False
            assert "finalizando" in msg
        finally:
            stop.set()
            t.join(timeout=5)

    def test_stop_scan_async_noop_when_not_running(self):
        bridge = StrixRuntimeBridge()
        assert bridge.stop_scan_async() is False

    def test_stop_scan_async_runs_in_background_and_reports(self):
        """stop_scan_async returns immediately and reports the honest result via on_done."""
        import time as _time

        bridge = StrixRuntimeBridge()
        # Make is_running True via a live scan task
        loop = asyncio.new_event_loop()

        async def _long():
            await asyncio.sleep(30)

        bridge._scan_task = loop.create_task(_long())
        bridge._loop = loop
        bridge._runtime = None  # stop_scan returns True quickly (no quit/join)
        bridge._thread = None

        result = []
        started = _time.monotonic()
        try:
            assert bridge.is_running is True
            initiated = bridge.stop_scan_async(on_done=lambda ok: result.append(ok))
            elapsed = _time.monotonic() - started
            assert initiated is True
            # Non-blocking: returned well before the 30s sleep / any join
            assert elapsed < 2.0
            # Wait for the background worker to report
            deadline = _time.monotonic() + 5.0
            while not result and _time.monotonic() < deadline:
                _time.sleep(0.05)
            assert result == [True]
        finally:
            bridge._scan_task.cancel()
            loop.run_until_complete(asyncio.gather(bridge._scan_task, return_exceptions=True))
            loop.close()

    def test_is_actively_working_reads_coordinator(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "running"}
        bridge._root_agent_id = "root"

        loop = asyncio.new_event_loop()
        async def noop():
            await asyncio.sleep(100)
        bridge._scan_task = loop.create_task(noop())
        bridge._loop = loop
        try:
            assert bridge.is_actively_working is True
            assert bridge.is_running is True

            bridge._coordinator.statuses = {"root": "waiting"}
            assert bridge.is_actively_working is False
            assert bridge.is_running is True

            bridge._scan_completed = True
            bridge._scan_task.cancel()
            loop.run_until_complete(asyncio.gather(bridge._scan_task, return_exceptions=True))
            bridge._scan_task = None
            assert bridge.is_actively_working is False
            assert bridge.is_running is False
        finally:
            if not loop.is_closed():
                loop.close()

    def test_check_waiting_notification(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        import asyncio
        import threading as _threading

        loop = asyncio.new_event_loop()
        bridge._loop = loop

        async def mock_wait_kind(agent_id):
            return "user"

        bridge._coordinator.wait_kind_of = mock_wait_kind

        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ev = bridge.check_waiting_notification()
            assert ev is not None
            assert ev["data"]["event"] == "agent_waiting"
            assert ev["data"]["content"] == "strix-agent"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

    def test_fresh_live_view_on_start_scan_init(self):
        bridge = StrixRuntimeBridge()
        bridge._live_view = MagicMock(events=[{"id": "test_1", "type": "chat", "data": {}}])
        bridge.poll_events()

        bridge._run_name = "active"
        ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output="done")
        with bridge._lv_lock:
            bridge._live_view.ingest_sdk_event("a1", ev)
            bridge._live_view.events.append({"id": "sdk_1", "type": "chat", "data": {"role": "assistant", "content": "done"}})
        events = bridge.poll_events()
        assert len(events) >= 1
        assert events[-1]["type"] == "chat"

    def test_get_agent_tree_from_live_view(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.graph_snapshot = AsyncMock(
            return_value=(
                {"a1": None, "a2": "a1"},
                {"a1": "running", "a2": "waiting"},
                {"a1": "root", "a2": "child"},
                {},
            )
        )
        loop = asyncio.new_event_loop()
        bridge._loop = loop
        import threading as _th
        loop_thread = _th.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        try:
            tree = bridge.get_agent_tree()
            assert tree is not None
            assert "a1" in tree["agents"]
            assert "a2" in tree["agents"]
            assert tree["agents"]["a1"]["name"] == "root"
            assert tree["agents"]["a2"]["parent_id"] == "a1"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

    def test_get_agent_tree_none_when_no_live_view(self):
        bridge = StrixRuntimeBridge()
        tree = bridge.get_agent_tree()
        assert tree is None

    def test_list_agents_from_live_view(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._coordinator.graph_snapshot = AsyncMock(
            return_value=(
                {"a1": None, "a2": None},
                {"a1": "running", "a2": "running"},
                {"a1": "agent1", "a2": "agent2"},
                {},
            )
        )
        loop = asyncio.new_event_loop()
        bridge._loop = loop
        import threading as _th
        loop_thread = _th.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        try:
            agents = bridge.list_agents()
            assert len(agents) == 2
            names = {a["name"] for a in agents}
            assert "agent1" in names
            assert "agent2" in names
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

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
        mock_lv = MagicMock()
        mock_lv.events = []
        mock_lv.ingest_sdk_event = MagicMock(side_effect=lambda agent_id, ev: mock_lv.events.append(ev))
        bridge._live_view = mock_lv

        errors = []
        barrier = threading.Barrier(2, timeout=5)

        def writer():
            try:
                barrier.wait()
                for i in range(100):
                    ev = _make_sdk_event("run_item_stream_event", item_type="message_output_item", output=f"msg{i}")
                    with bridge._lv_lock:
                        bridge._live_view.ingest_sdk_event(f"a{i}", ev)
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

        loop = asyncio.new_event_loop()
        async def noop():
            await asyncio.sleep(100)
        bridge._scan_task = loop.create_task(noop())
        bridge._loop = loop
        try:
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
            bridge._scan_task.cancel()
            loop.run_until_complete(asyncio.gather(bridge._scan_task, return_exceptions=True))
            bridge._scan_task = None
            assert bridge.is_running is False
        finally:
            if not loop.is_closed():
                loop.close()

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

        loop = asyncio.new_event_loop()
        async def noop():
            await asyncio.sleep(100)
        bridge._scan_task = loop.create_task(noop())
        bridge._loop = loop
        try:
            assert bridge.is_running is True
            assert bridge._scan_completed is False
        finally:
            bridge._scan_task.cancel()
            loop.run_until_complete(asyncio.gather(bridge._scan_task, return_exceptions=True))
            loop.close()

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
        mock_lv = MagicMock()
        mock_lv.events = []
        mock_lv.ingest_sdk_event = MagicMock(side_effect=lambda agent_id, ev: mock_lv.events.append(ev))
        bridge._live_view = mock_lv

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
    def test_diff_scope_inactive_for_url_only(self):
        """When scope_mode=auto and targets are URLs (no local repos),
        diff_scope.active should be False."""
        bridge = StrixRuntimeBridge()
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://example.com"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
        assert info[0]["type"] == "web_application"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.prepare_run")
    def test_prepare_run_called_via_start_scan(self, mock_prepare, mock_itt,
                                               monkeypatch, tmp_path):
        """start_scan() must delegate to prepare_run for target resolution and diff scope."""
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})

        import threading as _threading
        release_scan = _threading.Event()

        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)

        bridge = StrixRuntimeBridge()

        def mock_runtime_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0

            async def blocking_task():
                while not release_scan.is_set():
                    await asyncio.sleep(0.02)

            mock_runtime.scan_task = asyncio.ensure_future(blocking_task())
            return mock_runtime

        bridge._GoTuiRuntime = mock_runtime_factory
        try:
            ok, msg = bridge.start_scan(
                targets=["https://example.com"],
                instruction="test",
                scan_mode="deep",
                scope_mode="auto",
            )
            assert ok is True
        finally:
            release_scan.set()
            thread = bridge._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=10)

        mock_prepare.assert_called_once()
        call_args = mock_prepare.call_args
        ns = call_args[0][0]
        assert ns.scope_mode == "auto"
        assert ns.non_interactive is False

        assert bridge._current_targets == ["https://example.com"]
        assert bridge._run_name is not None
        assert bridge._run_name.startswith("scan-")

    def test_diff_scope_inactive_for_urls_without_local(self):
        """No local sources → diff_scope stays inactive (API not called)."""
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://example.com"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
        assert info[0]["type"] == "web_application"

    def test_prepare_run_contract_real(self, monkeypatch):
        """Contract test: real Strix prepare_run() receives valid args."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge, _STRIX_AVAILABLE
        if not _STRIX_AVAILABLE:
            pytest.skip("Strix not installed")

        from strix.interface.scan_setup import prepare_run as real_prepare_run
        from types import SimpleNamespace

        bridge = StrixRuntimeBridge()

        # Build args exactly as start_scan does (with all required fields)
        args = SimpleNamespace(
            run_name="contract-test-run",
            targets_info=[
                {"type": "url", "details": {"target_url": "https://example.com"}, "original": "https://example.com"}
            ],
            instruction="test instruction",
            scan_mode="deep",
            diff_scope={"active": False},
            scope_mode="auto",
            diff_base=None,
            local_sources=[],
            user_explicit_instruction="",
            max_budget_usd=None,
            max_turns=500,
            needs_setup=False,
            workspace_mount=None,
            resume=None,
            non_interactive=False,
            target=["https://example.com"],
            target_list=[],
        )

        # Call real prepare_run — should NOT raise
        real_prepare_run(args)

        # Verify prepare_run mutated args correctly
        assert args.run_name is not None
        assert len(args.run_name) > 0
        assert hasattr(args, "local_sources")
        assert isinstance(args.local_sources, list)


# ── Fix 1: Google Drive stays web_application, no artifact type ──
class TestFileHostingURLs:
    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_google_drive_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://drive.google.com/file/d/1abc/view"})
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://drive.google.com/file/d/1abc/view?usp=drivesdk"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
        assert len(info) == 1
        assert info[0]["type"] == "web_application"
        assert "drive.google.com" in info[0]["details"]["target_url"]

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_dropbox_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://www.dropbox.com/s/abc/file.apk"})
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://www.dropbox.com/s/abc/file.apk"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
        assert len(info) == 1
        assert info[0]["type"] == "web_application"

    def test_regular_url_unaffected(self):
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://example.com"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
        assert len(info) == 1
        assert info[0]["type"] == "web_application"

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    def test_mega_stays_web_application(self, mock_itt):
        mock_itt.return_value = ("web_application", {"target_url": "https://mega.nz/file/abc/file.apk"})
        from strix.interface.scan_setup import build_targets_info
        from types import SimpleNamespace
        args = SimpleNamespace(target=["https://mega.nz/file/abc/file.apk"], target_list=[])
        build_targets_info(args)
        info = args.targets_info
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
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        import asyncio
        import threading as _threading

        loop = asyncio.new_event_loop()
        bridge._loop = loop

        async def mock_wait_kind(agent_id):
            return "agents"

        bridge._coordinator.wait_kind_of = mock_wait_kind

        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ev = bridge.check_waiting_notification()
            assert ev is None
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

    def test_waiting_notification_suppressed_when_children_waiting(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        import asyncio
        import threading as _threading

        loop = asyncio.new_event_loop()
        bridge._loop = loop

        async def mock_wait_kind(agent_id):
            return "stalled"

        bridge._coordinator.wait_kind_of = mock_wait_kind

        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ev = bridge.check_waiting_notification()
            assert ev is None
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

    def test_waiting_notification_fires_when_children_all_done(self):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = {"root": "waiting"}
        bridge._root_agent_id = "root"
        from strix_telegram_bot.strix.runtime_bridge import TuiLiveView
        bridge._live_view = TuiLiveView()
        bridge._live_view.upsert_agent("root", name="strix-agent")

        import asyncio
        import threading as _threading

        loop = asyncio.new_event_loop()
        bridge._loop = loop

        async def mock_wait_kind(agent_id):
            return "user"

        bridge._coordinator.wait_kind_of = mock_wait_kind

        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        try:
            ev = bridge.check_waiting_notification()
            assert ev is not None
            assert ev["data"]["event"] == "agent_waiting"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None

    def test_no_non_interactive_contract_in_bridge(self):
        """The bridge always runs interactive: start_scan has no
        non_interactive parameter and scan_config forces it to False."""
        import inspect
        sig = inspect.signature(StrixRuntimeBridge.start_scan)
        assert "non_interactive" not in sig.parameters
        assert not hasattr(StrixRuntimeBridge(), "_non_interactive")

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

    def test_derive_terminal_kind_completed(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        bridge._run_name = "scan-test"
        mock_rs = MagicMock()
        mock_rs.run_name = "scan-test"
        mock_rs.run_record = {"status": "completed"}
        runtime = MagicMock()
        runtime.report_state = mock_rs
        bridge._runtime = runtime
        with patch("strix_telegram_bot.strix.runtime_bridge._report_md_present", return_value=True):
            kind = bridge._derive_terminal_kind()
        assert kind == "completed"

    def test_derive_terminal_kind_stopped(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "stopped"}
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "running"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            kind = bridge._derive_terminal_kind()
        assert kind == "stopped"

    def test_derive_terminal_kind_failed(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "failed"}
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "running"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            kind = bridge._derive_terminal_kind()
        assert kind == "failed"

    def test_derive_terminal_kind_inconsistent_uses_failed(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "waiting"}
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "running"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            kind = bridge._derive_terminal_kind()
        assert kind == "failed"


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
        assert "send_user_message_to_agent(" in src
        assert "record_user_message" not in src
        assert "coordinator.send(" not in src

    def test_diff_scope_always_called(self):
        """Fix 3: resolve_diff_scope_context is called unconditionally (no has_local_sources guard)."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        assert "has_local_sources" not in src

    def test_diff_scope_non_interactive_false(self):
        """prepare_run now receives non_interactive=False from start_scan."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge.start_scan)
        assert "non_interactive=False" in src

    def test_diff_scope_instruction_prepend(self):
        """_scan_thread delegates diff resolution to prepare_run."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "prepare_run(" in src

    def test_scan_thread_uses_gotuiruntime(self):
        """_scan_thread creates GoTuiRuntime and calls init_run_state + start_scan."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "_GoTuiRuntime(" in src
        assert "init_run_state()" in src
        assert "start_scan()" in src

    def test_scan_thread_polls_root(self):
        """_scan_thread polls coordinator for root agent discovery."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "_poll_root" in src


class TestWatcherBehavior:
    """Tests for derive_terminal_kind and completion detection."""

    def test_derive_terminal_kind_completed_and_persisted(self):
        """root completed + run_record completed + report present → completed."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        bridge._last_error = None
        bridge._run_name = "scan-test"
        mock_rs = MagicMock()
        mock_rs.run_name = "scan-test"
        mock_rs.run_record = {"status": "completed"}
        runtime = MagicMock()
        runtime.report_state = mock_rs
        bridge._runtime = runtime
        with patch("strix_telegram_bot.strix.runtime_bridge._report_md_present", return_value=True):
            kind = bridge._derive_terminal_kind()
        assert kind == "completed"

    def test_derive_terminal_kind_stopped_not_completed(self):
        """root stopped → stopped."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "stopped"}
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "running"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs):
            kind = bridge._derive_terminal_kind()
        assert kind == "stopped"

    def test_derive_terminal_kind_completed_no_report_still_success(self):
        """completed + run_record completed + no report file → failed (incomplete)."""
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        bridge._coordinator.statuses = {"root": "completed"}
        bridge._run_name = "scan-test"
        mock_rs = MagicMock()
        mock_rs.run_record = {"status": "completed"}
        with patch("strix_telegram_bot.strix.runtime_bridge._get_report_state", return_value=mock_rs), \
             patch("strix_telegram_bot.strix.runtime_bridge._report_md_present", return_value=False):
            kind = bridge._derive_terminal_kind()
        assert kind == "failed"


class TestReportMdPresent:
    def test_true_for_non_empty_file(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix.runtime_bridge import _report_md_present
        from pathlib import Path
        run_dir = tmp_path / "scan-report"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Informe\n")
        monkeypatch.setattr("strix_telegram_bot.strix.runtime_bridge._run_dir_for",
                            lambda name: run_dir)
        assert _report_md_present("scan-report") is True

    def test_false_for_missing_file(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix.runtime_bridge import _report_md_present
        monkeypatch.setattr("strix_telegram_bot.strix.runtime_bridge._run_dir_for",
                            lambda name: tmp_path / name)
        assert _report_md_present("scan-missing") is False

    def test_false_for_empty_file(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix.runtime_bridge import _report_md_present
        run_dir = tmp_path / "scan-empty"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("")
        monkeypatch.setattr("strix_telegram_bot.strix.runtime_bridge._run_dir_for",
                            lambda name: run_dir)
        assert _report_md_present("scan-empty") is False

    def test_false_for_blank_run_name(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix.runtime_bridge import _report_md_present
        monkeypatch.setattr("strix_telegram_bot.strix.runtime_bridge._run_dir_for",
                            lambda name: tmp_path / name)
        assert _report_md_present("") is False


class TestFinalizerSingleEvent:
    """Fix 6: _main's finalizer emits exactly ONE final event and persists run state."""

    def _make_bridge(self, monkeypatch, tmp_path, run_name, root_status):
        from strix_telegram_bot.strix import runtime_bridge as rb
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.setattr(rb, "prepare_run", lambda args: None)

        bridge = rb.StrixRuntimeBridge()
        bridge._run_name = run_name
        bridge._coordinator = MagicMock()
        bridge._coordinator.parent_of = None
        bridge._coordinator.statuses = {"root": root_status}
        bridge._root_agent_id = "root"
        return bridge

    def _make_mock_runtime(self, scan_task_coro=None, error=None):
        mock_runtime = MagicMock()
        mock_runtime.coordinator = MagicMock()
        mock_runtime.coordinator.parent_of = {"root": None}
        mock_runtime.coordinator.statuses = {"root": "running"}
        mock_runtime.live_view = MagicMock()
        mock_runtime.live_view.events = []
        mock_runtime.live_view._next_event_id = 0

        _error = error
        _scan_task_coro = scan_task_coro

        class _RuntimeProps:
            pass

        props = _RuntimeProps()

        def _get_scan_task():
            if hasattr(props, '_scan_task'):
                return props._scan_task
            loop = asyncio.get_event_loop()
            if _error:
                async def failing_task():
                    raise _error
                props._scan_task = loop.create_task(failing_task())
            elif _scan_task_coro:
                props._scan_task = loop.create_task(_scan_task_coro())
            else:
                async def noop_task():
                    await asyncio.sleep(100)
                props._scan_task = loop.create_task(noop_task())
            return props._scan_task

        type(mock_runtime).scan_task = property(lambda self: _get_scan_task())
        return mock_runtime

    def test_success_sets_terminal_completed(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix import runtime_bridge as rb
        run_name = "scan-ok"
        run_dir = tmp_path / run_name
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Informe\n")
        bridge = self._make_bridge(monkeypatch, tmp_path, run_name, "completed")

        async def immediate_complete():
            return None
        mock_runtime = self._make_mock_runtime(scan_task_coro=immediate_complete)
        mock_runtime.coordinator.statuses = {"root": "completed"}

        rs = MagicMock()
        rs.run_name = run_name
        rs.run_record = {"status": "completed"}

        mock_runtime.report_state = rs
        bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)
        with patch.object(rb, "_run_dir_for", lambda name: run_dir):
            from types import SimpleNamespace
            bridge._scan_thread(SimpleNamespace(
                max_turns=10, run_name="scan-ok",
                targets_info=[], instruction="", scan_mode="deep",
                diff_scope={"active": False}, scope_mode="auto",
                diff_base=None, local_sources=[], needs_setup=False,
            ))

        assert bridge._terminal_kind == "completed"
        assert bridge._scan_completed is True

    def test_exception_persists_failed_and_sets_terminal(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix import runtime_bridge as rb
        run_name = "scan-fail"
        bridge = self._make_bridge(monkeypatch, tmp_path, run_name, "running")

        mock_runtime = self._make_mock_runtime(error=RuntimeError("provider boom"))
        bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)

        rs = MagicMock()
        rs.run_name = run_name
        rs.run_record = {"status": "running"}

        mock_runtime.report_state = rs
        from types import SimpleNamespace
        bridge._scan_thread(SimpleNamespace(
            max_turns=10, run_name="scan-fail",
            targets_info=[], instruction="", scan_mode="deep",
            diff_scope={"active": False}, scope_mode="auto",
            diff_base=None, local_sources=[], needs_setup=False,
        ))

        assert bridge._terminal_kind == "failed"
        assert bridge._last_error == "provider boom"
        assert bridge._scan_completed is True
        rs.save_run_data.assert_any_call(status="failed")

    def test_cancel_persists_stopped_and_sets_terminal(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix import runtime_bridge as rb
        run_name = "scan-stop"
        bridge = self._make_bridge(monkeypatch, tmp_path, run_name, "running")

        mock_runtime = self._make_mock_runtime(error=asyncio.CancelledError())
        bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)

        rs = MagicMock()
        rs.run_name = run_name
        rs.run_record = {"status": "running"}

        mock_runtime.report_state = rs
        from types import SimpleNamespace
        bridge._scan_thread(SimpleNamespace(
            max_turns=10, run_name="scan-stop",
            targets_info=[], instruction="", scan_mode="deep",
            diff_scope={"active": False}, scope_mode="auto",
            diff_base=None, local_sources=[], needs_setup=False,
        ))

        assert bridge._user_cancelled is True
        assert bridge._terminal_kind == "stopped"
        assert bridge._scan_completed is True
        rs.save_run_data.assert_any_call(status="stopped")

    def test_failure_writes_end_time_in_run_json(self, monkeypatch, tmp_path):
        """Failure must persist failed + end_time in the real run.json (spec A)."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        from strix.report.state import ReportState as RealState, get_global_report_state
        run_name = "scan-endtime-fail"
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)

        prev_global = get_global_report_state()
        try:
            bridge = rb.StrixRuntimeBridge()
            bridge._run_name = run_name
            bridge._coordinator = MagicMock()
            bridge._coordinator.parent_of = None
            bridge._coordinator.statuses = {"root": "running"}
            bridge._root_agent_id = "root"

            mock_runtime = self._make_mock_runtime(error=RuntimeError("original provider error"))
            bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)

            rs = RealState(run_name=run_name)
            rs.set_scan_config({"targets": [{"type": "url", "url": "https://example.com"}]})

            mock_runtime.report_state = rs
            with patch.object(rb, "prepare_run", lambda args: None):
                from types import SimpleNamespace
                bridge._scan_thread(SimpleNamespace(
                    max_turns=10, run_name=run_name,
                    targets_info=[], instruction="", scan_mode="deep",
                    diff_scope={"active": False}, scope_mode="auto",
                    diff_base=None, local_sources=[], needs_setup=False,
                ))
        finally:
            from strix.report.state import set_global_report_state
            set_global_report_state(prev_global)

        run_json_path = tmp_path / "strix_runs" / run_name / "run.json"
        assert run_json_path.is_file()
        data = json.loads(run_json_path.read_text())
        assert data.get("status") == "failed"
        assert data.get("end_time") is not None

    def test_stop_writes_end_time_in_run_json(self, monkeypatch, tmp_path):
        """Stop must persist stopped + end_time in the real run.json (spec B)."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        from strix.report.state import ReportState as RealState, get_global_report_state
        run_name = "scan-endtime-stop"
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)

        prev_global = get_global_report_state()
        try:
            bridge = rb.StrixRuntimeBridge()
            bridge._run_name = run_name
            bridge._coordinator = MagicMock()
            bridge._coordinator.parent_of = None
            bridge._coordinator.statuses = {"root": "running"}
            bridge._root_agent_id = "root"

            mock_runtime = self._make_mock_runtime(error=asyncio.CancelledError())
            bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)

            rs = RealState(run_name=run_name)
            rs.set_scan_config({"targets": [{"type": "url", "url": "https://example.com"}]})

            mock_runtime.report_state = rs
            with patch.object(rb, "prepare_run", lambda args: None):
                from types import SimpleNamespace
                bridge._scan_thread(SimpleNamespace(
                    max_turns=10, run_name=run_name,
                    targets_info=[], instruction="", scan_mode="deep",
                    diff_scope={"active": False}, scope_mode="auto",
                    diff_base=None, local_sources=[], needs_setup=False,
                ))
        finally:
            from strix.report.state import set_global_report_state
            set_global_report_state(prev_global)

        run_json_path = tmp_path / "strix_runs" / run_name / "run.json"
        assert run_json_path.is_file()
        data = json.loads(run_json_path.read_text())
        assert data.get("status") == "stopped"
        assert data.get("end_time") is not None


class TestStartupReadiness:
    """Spec 8.10: hermetic start_scan readiness contract.

    start_scan must report success only after the runner thread signals
    readiness, surface known startup errors, time out with an
    initialization error, and never spawn duplicate threads or leave
    orphan scans/tasks.  No real scan, no containers, no writes to the
    real strix_runs dir.
    """

    def _patch_scan_path(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix import runtime_bridge as rb
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rb, "_STRIX_AVAILABLE", True)
        monkeypatch.setattr(rb, "prepare_run", lambda args: None)

    def _restore_global_report_state(self):
        from strix.report.state import set_global_report_state
        set_global_report_state(None)

    def _make_mock_runtime_factory(self, release=None, error=None, task_cls=None):
        """Return a callable that creates mock GoTuiRuntime instances."""
        from strix_telegram_bot.strix import runtime_bridge as rb

        def factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0

            if error:
                async def failing_task():
                    raise error
                mock_runtime.scan_task = asyncio.ensure_future(failing_task())
            elif task_cls:
                mock_runtime.scan_task = asyncio.ensure_future(task_cls())
            else:
                async def blocking_task():
                    if release:
                        while not release.is_set():
                            await asyncio.sleep(0.02)
                    else:
                        await asyncio.sleep(100)
                mock_runtime.scan_task = asyncio.ensure_future(blocking_task())

            return mock_runtime

        return factory

    def test_startup_success(self, monkeypatch, tmp_path):
        """Runner thread confirms readiness → start_scan returns True."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)
        release = _threading.Event()

        bridge = rb.StrixRuntimeBridge()
        factory = self._make_mock_runtime_factory(release=release)
        bridge._GoTuiRuntime = factory
        try:
            ok, msg = bridge.start_scan(
                targets=["https://example.com"], instruction="",
            )
            assert ok is True
            assert "Escaneo iniciado:" in msg
            assert bridge._startup_ready.is_set()
            assert bridge._startup_error is None
            assert bridge._thread is not None and bridge._thread.is_alive()
            assert bridge._scan_task is not None
            assert bridge._run_name is not None and bridge._run_name.startswith("scan-")
        finally:
            release.set()
            if bridge._thread is not None and bridge._thread.is_alive():
                bridge._thread.join(timeout=10)
            self._restore_global_report_state()

        assert not bridge._thread.is_alive()
        assert bridge._scan_completed is True
        assert bridge._loop is None

    def test_startup_error_before_task(self, monkeypatch, tmp_path):
        """A failure while creating the runner task surfaces as startup error."""
        from strix_telegram_bot.strix import runtime_bridge as rb

        self._patch_scan_path(monkeypatch, tmp_path)

        bridge = rb.StrixRuntimeBridge()
        def failing_factory(args):
            raise RuntimeError("boom")
        bridge._GoTuiRuntime = failing_factory
        try:
            ok, msg = bridge.start_scan(
                targets=["https://example.com"], instruction="",
            )
            assert ok is False
            assert "boom" in msg
            assert bridge._startup_error == "boom"
            assert bridge._scan_task is None
        finally:
            if bridge._thread is not None and bridge._thread.is_alive():
                bridge._thread.join(timeout=10)
            self._restore_global_report_state()

        assert not bridge._thread.is_alive()
        assert bridge._loop is None

    def test_startup_timeout_returns_initialization_error(self, monkeypatch, tmp_path):
        """Readiness not confirmed → start_scan returns the timeout error."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)
        release = _threading.Event()

        class _FakeEvent(_threading.Event):
            def wait(self, timeout=None):
                if timeout is None:
                    return super().wait()
                return False

        bridge = rb.StrixRuntimeBridge()
        factory = self._make_mock_runtime_factory(release=release)
        bridge._GoTuiRuntime = factory
        with patch.object(rb.threading, "Event", _FakeEvent):
            try:
                ok, msg = bridge.start_scan(
                    targets=["https://example.com"], instruction="",
                )
                assert ok is False
                assert "timeout de arranque" in msg
                assert bridge._startup_abort.is_set()
                assert not bridge._thread.is_alive()
            finally:
                release.set()
                if bridge._thread is not None:
                    bridge._thread.join(timeout=10)
                self._restore_global_report_state()

        assert not bridge._thread.is_alive()
        assert bridge._starting is False
        assert bridge._loop is None

    def test_no_duplicate_thread_on_second_start(self, monkeypatch, tmp_path):
        """A second start_scan while running must not spawn a second thread."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)
        release = _threading.Event()

        bridge = rb.StrixRuntimeBridge()
        factory = self._make_mock_runtime_factory(release=release)
        bridge._GoTuiRuntime = factory
        try:
            ok, msg = bridge.start_scan(
                targets=["https://example.com"], instruction="",
            )
            assert ok is True
            first_thread = bridge._thread
            first_task = bridge._scan_task

            ok2, msg2 = bridge.start_scan(
                targets=["https://example.com"], instruction="",
            )
            assert ok2 is False
            assert "Ya hay" in msg2
            assert bridge._thread is first_thread
            assert bridge._scan_task is first_task
        finally:
            release.set()
            if bridge._thread is not None and bridge._thread.is_alive():
                bridge._thread.join(timeout=10)
            self._restore_global_report_state()

    def test_no_orphan_scan_no_writes_to_real_runs_dir(self, monkeypatch, tmp_path):
        """After a full start→teardown cycle: no orphan thread/task and the real
        strix_runs dir gains no new run directories."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        from strix_telegram_bot.config import settings
        import threading as _threading

        real_runs_dir = settings.strix_runs_dir
        before = set(real_runs_dir.glob("scan-*")) if real_runs_dir.is_dir() else set()

        self._patch_scan_path(monkeypatch, tmp_path)
        release = _threading.Event()

        bridge = rb.StrixRuntimeBridge()
        factory = self._make_mock_runtime_factory(release=release)
        bridge._GoTuiRuntime = factory
        try:
            ok, msg = bridge.start_scan(
                targets=["https://example.com"], instruction="",
            )
            assert ok is True
        finally:
            release.set()
            if bridge._thread is not None and bridge._thread.is_alive():
                bridge._thread.join(timeout=10)
            self._restore_global_report_state()

        after = set(real_runs_dir.glob("scan-*")) if real_runs_dir.is_dir() else set()
        assert after == before
        assert not bridge._thread.is_alive()
        assert bridge._scan_completed is True
        assert bridge.is_running is False
        assert bridge._loop is None

    def _wait_until_true(self, predicate, timeout=5.0):
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except (AttributeError, TypeError):
                pass
            _time.sleep(0.005)
        return False

    def _make_instant_timeout_event(self):
        """An Event whose timed wait always reports timeout (real untimed wait)."""
        import threading as _threading

        class _InstantTimeoutEvent(_threading.Event):
            def wait(self, timeout=None):
                if timeout is None:
                    return super().wait()
                return False

        return _InstantTimeoutEvent

    def _make_delayed_timeout_event(self):
        """An Event whose timed wait blocks on a test gate then reports timeout."""
        import threading as _threading

        gate = _threading.Event()

        class _DelayedTimeoutEvent(_threading.Event):
            def wait(self, timeout=None):
                if timeout is None:
                    return super().wait()
                gate.wait()
                return False

        return gate, _DelayedTimeoutEvent

    def test_abort_before_task_never_invokes_runner(self, monkeypatch, tmp_path):
        """4.1: timeout before the runner task exists → no runner call, thread
        joined, loop closed, abort flag set."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)

        real_new_event_loop = rb.asyncio.new_event_loop
        loop_arrived = _threading.Event()
        loop_gate = _threading.Event()

        def gated_new_event_loop():
            loop_arrived.set()
            loop_gate.wait()
            return real_new_event_loop()

        bridge = rb.StrixRuntimeBridge()
        result = {}
        instant_timeout_event = self._make_instant_timeout_event()

        factory_called = _threading.Event()

        def slow_factory(args):
            factory_called.set()
            return self._make_mock_runtime_factory()(args)

        bridge._GoTuiRuntime = slow_factory
        with patch.object(rb.asyncio, "new_event_loop", gated_new_event_loop), \
             patch.object(rb.threading, "Event", instant_timeout_event):
            worker = _threading.Thread(
                target=lambda: result.update(
                    {"ret": bridge.start_scan(
                        targets=["https://example.com"], instruction="")}),
                daemon=True,
            )
            worker.start()
            try:
                assert loop_arrived.wait(timeout=10)
                assert bridge._thread is not None and bridge._thread.is_alive()
                assert bridge._starting is True
                assert self._wait_until_true(
                    lambda: bridge._startup_abort.is_set(), timeout=2.0)
            finally:
                loop_gate.set()
                worker.join(timeout=15)
                if bridge._thread is not None and bridge._thread.is_alive():
                    bridge._thread.join(timeout=10)
                self._restore_global_report_state()

        ok, msg = result["ret"]
        assert ok is False
        assert "timeout de arranque" in msg
        assert bridge._startup_abort.is_set()
        assert bridge._scan_task is None
        assert not bridge._thread.is_alive()
        assert bridge._loop is None
        assert bridge._starting is False

    def test_abort_after_task_cancels_runner(self, monkeypatch, tmp_path):
        """4.2: timeout after the runner task exists → task cancelled, runner
        never continues after start_scan returns, thread joined, second start
        rejected while the abort is still winding down."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)

        gate, delayed_event = self._make_delayed_timeout_event()
        runner_started = _threading.Event()
        release = _threading.Event()

        def controlled_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0

            async def blocking_task():
                runner_started.set()
                while not release.is_set():
                    await asyncio.sleep(0.02)

            mock_runtime.scan_task = asyncio.ensure_future(blocking_task())
            return mock_runtime

        bridge = rb.StrixRuntimeBridge()
        result = {}
        bridge._GoTuiRuntime = controlled_factory
        with patch.object(rb.threading, "Event", delayed_event):
            worker = _threading.Thread(
                target=lambda: result.update(
                    {"ret": bridge.start_scan(
                        targets=["https://example.com"], instruction="")}),
                daemon=True,
            )
            worker.start()
            try:
                assert runner_started.wait(timeout=10)
                first_thread = bridge._thread
                first_task = bridge._scan_task
                assert first_thread is not None and first_thread.is_alive()
                assert first_task is not None and not first_task.done()
                assert bridge._starting is True
                assert bridge._loop is not None

                ok2, msg2 = bridge.start_scan(
                    targets=["https://example.com"], instruction="")
                assert ok2 is False
                assert "Ya hay" in msg2
                assert bridge._thread is first_thread
                assert bridge._scan_task is first_task
            finally:
                gate.set()
                worker.join(timeout=15)
                release.set()
                if bridge._thread is not None and bridge._thread.is_alive():
                    bridge._thread.join(timeout=10)
                self._restore_global_report_state()

        ok, msg = result["ret"]
        assert ok is False
        assert "timeout de arranque" in msg
        assert bridge._startup_abort.is_set()
        assert not bridge._thread.is_alive()
        assert bridge._loop is None
        assert bridge._starting is False

    def test_second_start_rejected_during_initialization(self, monkeypatch, tmp_path):
        """4.3: a second start while the first scan is still initializing is
        rejected and must not touch the existing thread/task."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading

        self._patch_scan_path(monkeypatch, tmp_path)

        gate, delayed_event = self._make_delayed_timeout_event()
        runner_started = _threading.Event()
        release = _threading.Event()

        def controlled_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0

            async def blocking_task():
                runner_started.set()
                while not release.is_set():
                    await asyncio.sleep(0.02)

            mock_runtime.scan_task = asyncio.ensure_future(blocking_task())
            return mock_runtime

        bridge = rb.StrixRuntimeBridge()
        result = {}
        bridge._GoTuiRuntime = controlled_factory
        with patch.object(rb.threading, "Event", delayed_event):
            worker = _threading.Thread(
                target=lambda: result.update(
                    {"ret": bridge.start_scan(
                        targets=["https://example.com"], instruction="")}),
                daemon=True,
            )
            worker.start()
            try:
                assert runner_started.wait(timeout=10)
                first_thread = bridge._thread
                first_task = bridge._scan_task
                assert bridge._starting is True

                ok2, msg2 = bridge.start_scan(
                    targets=["https://example.com"], instruction="")
                assert ok2 is False
                assert "Ya hay" in msg2
                assert bridge._thread is first_thread
                assert bridge._scan_task is first_task
                assert bridge._run_name is not None
            finally:
                gate.set()
                worker.join(timeout=15)
                release.set()
                if bridge._thread is not None and bridge._thread.is_alive():
                    bridge._thread.join(timeout=10)
                self._restore_global_report_state()

        ok, msg = result["ret"]
        assert ok is False
        assert "timeout de arranque" in msg

    def test_stuck_thread_fails_closed(self, monkeypatch, tmp_path):
        """4.4: a scan thread that refuses to die leaves the bridge blocked
        (fail closed): identifiable grave error, same thread object kept, a
        second start also fails (timeout), and the join timeout is bounded."""
        from strix_telegram_bot.strix import runtime_bridge as rb
        import threading as _threading
        import time as _time

        self._patch_scan_path(monkeypatch, tmp_path)

        gate, delayed_event = self._make_delayed_timeout_event()
        runner_started = _threading.Event()
        release = _threading.Event()

        def stuck_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0

            async def blocking_task():
                runner_started.set()
                while not release.is_set():
                    pass

            mock_runtime.scan_task = asyncio.ensure_future(blocking_task())
            return mock_runtime

        bridge = rb.StrixRuntimeBridge()
        result = {}
        bridge._GoTuiRuntime = stuck_factory
        with patch.object(rb.threading, "Event", delayed_event):
            worker = _threading.Thread(
                target=lambda: result.update(
                    {"ret": bridge.start_scan(
                        targets=["https://example.com"], instruction="")}),
                daemon=True,
            )
            started = _time.monotonic()
            worker.start()
            try:
                assert runner_started.wait(timeout=10)
                first_thread = bridge._thread

                gate.set()
                worker.join(timeout=30)
                elapsed = _time.monotonic() - started

                ok, msg = result["ret"]
                assert ok is False
                assert "no terminó" in msg
                assert bridge._startup_abort.is_set()
                assert bridge._thread is first_thread
                assert bridge._thread.is_alive()
                assert bridge._starting is True
                assert bridge._scan_completed is False

                # With the new lifecycle, is_running returns False when
                # _startup_abort is set, so the second start proceeds but
                # also fails with a timeout.
                ok2, msg2 = bridge.start_scan(
                    targets=["https://example.com"], instruction="")
                assert ok2 is False
            finally:
                release.set()
                if bridge._thread is not None and bridge._thread.is_alive():
                    bridge._thread.join(timeout=10)
                self._restore_global_report_state()

        assert not bridge._thread.is_alive()
        assert bridge._starting is False
        assert elapsed < 40


class TestCleanupCountSingle:
    """Spec 8.9: the official runner owns cleanup; the bridge never calls
    session_manager.cleanup.  Total cleanup count == 1."""

    def test_cleanup_count_is_one_runner_owned(self, monkeypatch, tmp_path):
        from strix_telegram_bot.strix import runtime_bridge as rb
        from unittest.mock import MagicMock

        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)

        run_name = "scan-cleanup-1"
        run_dir = tmp_path / run_name
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Informe\n")

        bridge = rb.StrixRuntimeBridge()
        bridge._run_name = run_name
        bridge._root_agent_id = "root"

        mock_runtime = MagicMock()
        mock_runtime.coordinator = MagicMock()
        mock_runtime.coordinator.parent_of = {"root": None}
        mock_runtime.coordinator.statuses = {"root": "completed"}
        mock_runtime.live_view = MagicMock()
        mock_runtime.live_view.events = []
        mock_runtime.live_view._next_event_id = 0

        async def immediate_complete():
            return None

        _task_holder = [None]

        def _get_task():
            if _task_holder[0] is None or _task_holder[0].done():
                loop = asyncio.get_event_loop()
                _task_holder[0] = loop.create_task(immediate_complete())
            return _task_holder[0]

        type(mock_runtime).scan_task = property(lambda self: _get_task())

        bridge._GoTuiRuntime = MagicMock(return_value=mock_runtime)

        rs = MagicMock()
        rs.run_name = run_name
        rs.run_record = {"status": "completed"}

        mock_runtime.report_state = rs

        with patch.object(rb, "prepare_run", lambda args: None), \
             patch.object(rb, "_run_dir_for", lambda name: run_dir):
            from types import SimpleNamespace
            bridge._scan_thread(SimpleNamespace(
                max_turns=10, run_name="scan-cleanup-1",
                targets_info=[], instruction="", scan_mode="deep",
                diff_scope={"active": False}, scope_mode="auto",
                diff_base=None, local_sources=[], needs_setup=False,
            ))

            assert bridge._terminal_kind == "completed"
            assert bridge._scan_completed is True


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
        src = inspect.getsource(StrixRuntimeBridge._scan_thread)
        assert "prepare_run(" in src


class TestDiffScopeFailFast:
    """ValueError from prepare_run must fail _scan_thread before runtime creation."""

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.prepare_run")
    def test_valueerror_returns_false_no_thread(self, mock_prepare, mock_itt, monkeypatch, tmp_path):
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        mock_prepare.side_effect = ValueError("invalid scope: bad repo state")
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)
        bridge = StrixRuntimeBridge()

        def mock_runtime_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0
            import asyncio as _aio
            async def blocking():
                await _aio.sleep(100)
            mock_runtime.scan_task = _aio.ensure_future(blocking())
            return mock_runtime

        bridge._GoTuiRuntime = mock_runtime_factory
        ok, msg = bridge.start_scan(
            targets=["https://example.com"],
            instruction="test",
            scan_mode="deep",
            scope_mode="auto",
        )
        import time as _time
        _time.sleep(1.0)
        assert ok is False
        assert "preparación" in msg.lower() or "scope" in msg.lower()
        assert bridge._scan_completed is True

    @patch("strix_telegram_bot.strix.runtime_bridge.infer_target_type")
    @patch("strix_telegram_bot.strix.runtime_bridge.assign_workspace_subdirs", MagicMock())
    @patch("strix_telegram_bot.strix.runtime_bridge.prepare_run")
    def test_generic_exception_returns_false(self, mock_prepare, mock_itt, monkeypatch, tmp_path):
        mock_itt.return_value = ("url", {"target_url": "https://example.com"})
        mock_prepare.side_effect = RuntimeError("unexpected failure")
        monkeypatch.setattr("strix_telegram_bot.config.settings.strix_runs_dir", tmp_path)
        monkeypatch.chdir(tmp_path)
        bridge = StrixRuntimeBridge()

        def mock_runtime_factory(args):
            mock_runtime = MagicMock()
            mock_runtime.coordinator = MagicMock()
            mock_runtime.coordinator.parent_of = None
            mock_runtime.coordinator.statuses = {"root": "running"}
            mock_runtime.live_view = MagicMock()
            mock_runtime.live_view.events = []
            mock_runtime.live_view._next_event_id = 0
            import asyncio as _aio
            async def blocking():
                await _aio.sleep(100)
            mock_runtime.scan_task = _aio.ensure_future(blocking())
            return mock_runtime

        bridge._GoTuiRuntime = mock_runtime_factory
        ok, msg = bridge.start_scan(
            targets=["https://example.com"],
            instruction="test",
            scan_mode="deep",
            scope_mode="auto",
        )
        import time as _time
        _time.sleep(1.0)
        assert ok is False
        assert bridge._scan_completed is True


class TestReportStatePerRunIsolation:
    """Radamanthys resolves per-run ReportState from the active GoTuiRuntime,
    NEVER from the module-level global. Run A must be immune to Run B's
    global state, and a runtime whose run_name mismatches the bridge's
    active run must be rejected."""

    def _make_bridge(self, run_name="run-a"):
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        bridge = StrixRuntimeBridge()
        bridge._run_name = run_name
        bridge._coordinator = MagicMock()
        bridge._root_agent_id = "root"
        return bridge

    def test_report_state_resolves_from_active_runtime_not_global(self):
        from strix.report.state import ReportState as RealState
        from strix.report.state import (
            get_global_report_state,
            set_global_report_state,
        )
        bridge = self._make_bridge("run-a")
        run_a_state = RealState(run_name="run-a")
        run_a_state.vulnerability_reports.append({"id": "A-1"})
        runtime = MagicMock()
        runtime.report_state = run_a_state
        bridge._runtime = runtime

        run_b_state = RealState(run_name="run-b")
        run_b_state.vulnerability_reports.append({"id": "B-1"})

        prev = get_global_report_state()
        try:
            set_global_report_state(run_b_state)
            assert bridge._report_state() is run_a_state
            assert bridge.get_vulnerabilities() == [{"id": "A-1"}]
        finally:
            set_global_report_state(prev)

    def test_global_run_b_does_not_affect_run_a_terminal_kind(self):
        from strix.report.state import ReportState as RealState
        from strix.report.state import (
            get_global_report_state,
            set_global_report_state,
        )
        bridge = self._make_bridge("run-a")
        bridge._coordinator.statuses = {"root": "completed"}
        run_a_state = RealState(run_name="run-a")
        run_a_state.run_record["status"] = "completed"
        runtime = MagicMock()
        runtime.report_state = run_a_state
        bridge._runtime = runtime

        run_b_state = RealState(run_name="run-b")
        run_b_state.run_record["status"] = "completed"

        prev = get_global_report_state()
        try:
            set_global_report_state(run_b_state)
            with patch(
                "strix_telegram_bot.strix.runtime_bridge._report_md_present",
                return_value=True,
            ):
                kind = bridge._derive_terminal_kind()
            assert kind == "completed"
        finally:
            set_global_report_state(prev)

    def test_mismatched_run_rejected(self):
        from strix.report.state import ReportState as RealState
        bridge = self._make_bridge("run-a")
        other_state = RealState(run_name="run-b")
        runtime = MagicMock()
        runtime.report_state = other_state
        bridge._runtime = runtime
        assert bridge._report_state() is None
        assert bridge.get_vulnerabilities() == []


class TestAwaitingUserAgents:
    """FASE 1: awaiting_user_agents() returns ONLY agents with
    status == 'waiting' AND wait_kind == 'user'. wait_kind 'agents'
    (waiting for children) and 'stalled' do not open the user channel."""

    def _make_bridge(self, statuses, wait_kinds):
        bridge = StrixRuntimeBridge()
        bridge._run_name = "test-run"
        bridge._coordinator = MagicMock()
        bridge._coordinator.statuses = statuses

        async def mock_wait_kind(agent_id):
            return wait_kinds.get(agent_id, "user")

        bridge._coordinator.wait_kind_of = mock_wait_kind

        import threading as _threading
        loop = asyncio.new_event_loop()
        bridge._loop = loop
        loop_thread = _threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        return bridge, loop, loop_thread

    def test_only_waiting_user_agents_returned(self):
        bridge, loop, loop_thread = self._make_bridge(
            {"root": "waiting", "a2": "waiting", "a3": "running"},
            {"root": "user", "a2": "agents", "a3": "user"},
        )
        try:
            result = bridge.awaiting_user_agents()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None
        ids = [a["id"] for a in result]
        assert ids == ["root"]
        assert result[0]["name"] == "root"

    def test_no_coordinator_returns_empty(self):
        bridge = StrixRuntimeBridge()
        bridge._coordinator = None
        assert bridge.awaiting_user_agents() == []

    def test_wait_kind_agents_does_not_open_channel(self):
        bridge, loop, loop_thread = self._make_bridge(
            {"root": "waiting"}, {"root": "agents"})
        try:
            result = bridge.awaiting_user_agents()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            bridge._loop = None
        assert result == []

    def test_last_agent_message_returns_last_assistant_content(self):
        bridge = StrixRuntimeBridge()
        lv = MagicMock()
        lv.events_for_agent.return_value = [
            {"type": "chat", "data": {"role": "assistant", "content": "primera"}},
            {"type": "tool", "data": {}},
            {"type": "chat", "data": {"role": "user", "content": "respuesta"}},
            {"type": "chat", "data": {"role": "assistant", "content": "¿Continúo?"}},
        ]
        bridge._live_view = lv
        assert bridge.last_agent_message("a1") == "¿Continúo?"
        lv.events_for_agent.assert_called_with("a1")
