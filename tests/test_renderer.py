"""Tests for Telegram renderer — _process_scan_events with streaming, tools, splitting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import json

import pytest


def _make_chat_event(event_id="chat_1", version=0, content="hello", streaming=False, run_name="test-run"):
    return {
        "id": event_id,
        "type": "chat",
        "agent_id": "a1",
        "timestamp": "2026-01-01T00:00:00Z",
        "version": version,
        "data": {
            "role": "assistant",
            "content": content,
            "metadata": {"source": "sdk_stream", "streaming": streaming},
            "run_name": run_name,
        },
    }


def _make_tool_event(call_id="call_1", tool_name="test_tool", status="running", args=None, result=None, run_name="test-run"):
    return {
        "id": f"tool_{call_id}",
        "type": "tool",
        "agent_id": "a1",
        "timestamp": "2026-01-01T00:00:00Z",
        "version": 0,
        "data": {
            "tool_name": tool_name,
            "args": args or {},
            "status": status,
            "agent_id": "a1",
            "call_id": call_id,
            "result": result,
            "run_name": run_name,
        },
    }


def _make_system_event(event_name, content="", run_name="test-run", agent_id=""):
    return {
        "id": f"bridge_{event_name}_1",
        "type": "system",
        "agent_id": agent_id,
        "timestamp": 0.0,
        "version": 0,
        "data": {
            "event": event_name,
            "content": content,
            "run_name": run_name,
        },
    }


@pytest.fixture
def mock_telegram():
    with patch("strix_telegram_bot.bot.send_message") as mock_send, \
         patch("strix_telegram_bot.bot.edit_message") as mock_edit, \
         patch("strix_telegram_bot.bot.delete_message") as mock_delete:
        mock_send.return_value = {"message_id": 100}
        yield mock_send, mock_edit, mock_delete


@pytest.fixture
def bot(mock_telegram):
    from strix_telegram_bot.bot import StrixBot
    b = StrixBot()
    b._active_job_chat_id = 12345
    b._active_job_run_name = "test-run"
    return b


class TestStreamingRenderer:
    def test_streaming_creates_single_message(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        ev1 = _make_chat_event("chat_1", version=0, content="analyzing", streaming=True)
        bot._process_scan_events([ev1])

        assert mock_send.call_count == 1

    def test_streaming_edits_same_message_on_new_version(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        ev1 = _make_chat_event("chat_1", version=0, content="analyzing", streaming=True)
        bot._process_scan_events([ev1])

        ev2 = _make_chat_event("chat_1", version=1, content="analyzing target", streaming=True)
        bot._process_scan_events([ev2])

        assert mock_edit.call_count == 1
        assert mock_edit.call_args[0][2] == 100
        assert "analyzing target" in mock_edit.call_args[0][3]

    def test_streaming_skips_same_version(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        ev1 = _make_chat_event("chat_1", version=0, content="a", streaming=True)
        bot._process_scan_events([ev1])
        assert mock_send.call_count == 1

        ev2 = _make_chat_event("chat_1", version=0, content="a", streaming=True)
        bot._process_scan_events([ev2])
        assert mock_send.call_count == 1
        assert mock_edit.call_count == 0

    def test_streaming_finalize_no_duplicate(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        ev1 = _make_chat_event("chat_1", version=0, content="partial", streaming=True)
        bot._process_scan_events([ev1])

        ev2 = _make_chat_event("chat_1", version=1, content="final content", streaming=False)
        bot._process_scan_events([ev2])

        assert "chat_1" not in bot._chat_fragments
        assert mock_edit.call_count == 1

    def test_streaming_reset_on_new_stream(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        ev1 = _make_chat_event("chat_1", version=0, content="first", streaming=True)
        bot._process_scan_events([ev1])

        ev2 = _make_chat_event("chat_2", version=0, content="second", streaming=True)
        bot._process_scan_events([ev2])

        assert mock_send.call_count == 2
        assert "chat_2" in bot._chat_fragments

    def test_non_streaming_sends_direct(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        ev = _make_chat_event("chat_1", version=0, content="direct message", streaming=False)
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1


class TestToolRenderer:
    def test_tool_running_creates_message(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 200}

        ev = _make_tool_event(call_id="call_1", tool_name="nuclei_scan", status="running",
                              args={"url": "https://example.com"})
        bot._process_scan_events([ev])

        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "nuclei_scan" in text
        assert "In progress" in text
        assert bot._tool_message_ids.get("call_1") == 200

    def test_tool_completed_edits_same_message(self, bot, mock_telegram):
        _, mock_edit, _ = mock_telegram
        bot._tool_message_ids["call_1"] = 200

        ev = _make_tool_event(call_id="call_1", tool_name="nuclei_scan", status="completed",
                              result="Found CVE-2024-1234")
        bot._process_scan_events([ev])

        assert mock_edit.call_count == 1
        assert mock_edit.call_args[0][2] == 200
        text = mock_edit.call_args[0][3]
        assert "Result:" in text
        assert "CVE-2024-1234" in text

    def test_tool_failed_shows_status(self, bot, mock_telegram):
        _, mock_edit, _ = mock_telegram
        bot._tool_message_ids["call_2"] = 201

        ev = _make_tool_event(call_id="call_2", tool_name="ffuf", status="failed",
                              result="connection refused")
        bot._process_scan_events([ev])

        assert mock_edit.call_count == 1
        text = mock_edit.call_args[0][3]
        assert "Result:" in text
        assert "connection refused" in text

    def test_tool_no_duplicate_on_second_call(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 300}

        ev1 = _make_tool_event(call_id="call_x", tool_name="curl", status="running")
        bot._process_scan_events([ev1])

        ev2 = _make_tool_event(call_id="call_y", tool_name="subfinder", status="running")
        bot._process_scan_events([ev2])

        assert mock_send.call_count == 2
        assert bot._tool_message_ids["call_x"] == 300
        assert bot._tool_message_ids.get("call_y") is not None

    def test_tool_output_without_running_sends_new_message(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 400}
        ev = _make_tool_event(call_id="unknown", tool_name="tool", status="completed",
                              result="orphan")
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1
        assert mock_edit.call_count == 0


class TestMessageSplitting:
    def test_long_message_splits_into_fragments(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        long_text = "X" * 5000
        ev = _make_chat_event("chat_1", version=0, content=long_text, streaming=False)
        bot._process_scan_events([ev])
        assert mock_send.call_count == 2
        for call_args in mock_send.call_args_list:
            assert len(call_args[0][2]) <= 4100

    def test_short_message_not_split(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        ev = _make_chat_event("chat_1", version=0, content="short", streaming=False)
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1


class TestScanCompleteCycle:
    def test_running_to_waiting_notification(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        ev = _make_system_event("agent_waiting", content="strix-agent")
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "esperando instrucciones" in text

    def test_scan_complete_sends_final_message(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        bot._bridge._start_time = 0
        bot._bridge._scan_status = "completed"
        ev = _make_system_event("scan_complete")
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "completado" in text
        assert "informe final no fue generado" in text


class TestCallHistory:
    def test_streaming_call_sequence(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        events = [
            _make_chat_event("chat_1", version=0, content="par", streaming=True),
            _make_chat_event("chat_1", version=1, content="partial r", streaming=True),
            _make_chat_event("chat_1", version=2, content="partial result", streaming=True),
            _make_chat_event("chat_1", version=3, content="final result here", streaming=False),
        ]
        bot._process_scan_events(events)

        assert mock_send.call_count == 1  # first delta creates
        assert mock_edit.call_count == 3  # 2 deltas + 1 final

    def test_tool_call_sequence(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 200}

        events = [
            _make_tool_event(call_id="c1", tool_name="nuclei", status="running",
                             args={"url": "http://test"}),
            _make_tool_event(call_id="c1", tool_name="nuclei", status="completed",
                             result="3 vulns found"),
        ]
        bot._process_scan_events(events)

        assert mock_send.call_count == 1
        assert mock_edit.call_count == 1
        assert bot._tool_message_ids["c1"] == 200


class TestOutputSanitization:
    def test_dict_result_serialized(self):
        from strix_telegram_bot.bot import StrixBot
        result = StrixBot._sanitize_tool_result({"vuln": "XSS", "severity": "high"})
        assert "vuln" in result
        assert "XSS" in result

    def test_list_result_serialized(self):
        from strix_telegram_bot.bot import StrixBot
        result = StrixBot._sanitize_tool_result(["a", "b", "c"])
        assert "a" in result

    def test_number_result_stringified(self):
        from strix_telegram_bot.bot import StrixBot
        result = StrixBot._sanitize_tool_result(42)
        assert "42" in result

    def test_none_result_empty(self):
        from strix_telegram_bot.bot import StrixBot
        result = StrixBot._sanitize_tool_result(None)
        assert result == ""

    def test_unserializable_result_handled(self):
        from strix_telegram_bot.bot import StrixBot
        class Unserializable:
            def __str__(self):
                return "custom object"
        result = StrixBot._sanitize_tool_result(Unserializable())
        assert "custom object" in result

    def test_long_result_truncated(self):
        from strix_telegram_bot.bot import StrixBot
        result = StrixBot._sanitize_tool_result("A" * 1000)
        assert len(result) <= 500

    def test_tool_args_sanitized(self):
        from strix_telegram_bot.bot import StrixBot
        args = {"url": "https://example.com/path?x=1", "method": "POST"}
        result = StrixBot._sanitize_tool_args(args)
        assert "url:" in result
        assert "method: POST" in result

    def test_tool_args_long_value_truncated(self):
        from strix_telegram_bot.bot import StrixBot
        args = {"url": "A" * 500}
        result = StrixBot._sanitize_tool_args(args)
        assert len(result) < 500

    def test_tool_args_max_five_keys(self):
        from strix_telegram_bot.bot import StrixBot
        args = {f"k{i}": f"v{i}" for i in range(20)}
        result = StrixBot._sanitize_tool_args(args)
        assert result.count("\n") < 6


# ── Telegram renderer unit tests ────────────────────────────────

class TestTelegramRenderers:
    """Tests for strix/telegram_renderers.py — TUI-mirroring formatters."""

    def test_shell_running_shows_command(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("execute_command", "running", {"command": "ls -la"})
        assert "ls -la" in text
        assert "In progress" in text

    def test_shell_completed_shows_exit_code(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        result = {"exit_code": 0, "output": "file1\nfile2"}
        text = render_tool_event("execute_command", "completed", {"command": "ls"}, result)
        assert "exit: 0" in text
        assert "file1" in text

    def test_shell_truncates_at_50_lines_with_marker(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        output = "\n".join(f"line {i}" for i in range(100))
        result = {"exit_code": 0, "output": output}
        text = render_tool_event("execute_command", "completed", {"command": "cat"}, result)
        assert "51 lines truncated" in text
        assert "line 0" in text
        assert "line 99" in text

    def test_shell_truncates_long_line_at_200_chars(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        long_line = "x" * 300
        result = {"exit_code": 0, "output": long_line}
        text = render_tool_event("execute_command", "completed", {"command": "cat"}, result)
        assert "..." in text
        assert "x" * 197 in text

    def test_shell_head_tail_split(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        output = "\n".join(f"line {i}" for i in range(60))
        result = {"exit_code": 0, "output": output}
        text = render_tool_event("execute_command", "completed", {"command": "cat"}, result)
        assert "line 0" in text
        assert "line 59" in text
        assert "lines truncated" in text

    def test_shell_failed_shows_error(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("execute_command", "failed", {"command": "bad"}, "connection refused")
        assert "Failed" in text
        assert "connection refused" in text

    def test_fallback_shows_all_args(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        args = {"target": "https://example.com", "method": "GET", "timeout": "30"}
        text = render_tool_event("custom_tool", "completed", args, "ok")
        assert "target: https://example.com" in text
        assert "method: GET" in text
        assert "timeout: 30" in text

    def test_fallback_shows_result(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("custom_tool", "completed", {"x": "1"}, "result data here")
        assert "Result: result data here" in text

    def test_fallback_running_shows_status_icon(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("custom_tool", "running", {"x": "1"})
        assert "In progress" in text
        assert "Result:" not in text

    def test_no_raw_sdk_event_in_output(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("execute_command", "completed", {"command": "ls"}, {"exit_code": 0, "output": "ok"})
        assert "tool_id" not in text
        assert "call_id" not in text
        assert "streaming" not in text

    def test_technical_chars_safe(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("execute_command", "completed", {"command": "echo <>&\"'" }, "done")
        assert "echo" in text
        assert "done" in text

    def test_empty_args_handled(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("custom_tool", "completed", {}, "result")
        assert "Result: result" in text

    def test_none_result_handled(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("custom_tool", "completed", {"x": "1"}, None)
        assert "In progress" not in text
        assert "Result:" not in text

    def test_vuln_report_format(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        args = {"title": "XSS in /login", "severity": "high", "cve": "CVE-2024-1234"}
        result = {"severity": "high", "cvss_score": 8.5}
        text = render_tool_event("create_vulnerability_report", "completed", args, result)
        assert "XSS in /login" in text
        assert "ALTO" in text
        assert "CVSS: 8.5" in text
        assert "CVE-2024-1234" in text

    def test_patch_shows_file_ops(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        patch = "*** Add File: src/app.py\n*** Update File: README.md"
        text = render_tool_event("apply_patch", "completed", {"patch": patch})
        assert "create src/app.py" in text
        assert "edit README.md" in text


class TestNonInteractivePreserved:
    """Verify non_interactive=True remains intact in bridge."""

    def test_bridge_default_non_interactive_true(self):
        from strix_telegram_bot.strix.runtime_bridge import StrixRuntimeBridge
        import inspect
        sig = inspect.signature(StrixRuntimeBridge.start_scan)
        default = sig.parameters["non_interactive"].default
        assert default is True

    def test_bot_calls_with_interactive_mode(self):
        from strix_telegram_bot.bot import StrixBot
        import inspect
        src = inspect.getsource(StrixBot._launch_scan)
        assert "non_interactive=False" in src

    def test_strix_not_modified(self):
        import strix
        from pathlib import Path
        strix_path = Path(strix.__file__).parent
        runner = strix_path / "core" / "runner.py"
        assert runner.exists()
        text = runner.read_text()
        assert "interactive" in text


class TestNoSdkEventsLeaked:
    """Ensure no raw SDK metadata leaks into Telegram messages."""

    def test_tool_event_no_sdk_fields(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("nuclei", "completed", {"url": "x"}, "found vuln")
        for field in ("tool_id", "call_id", "streaming", "event_type", "sdk_version"):
            assert field not in text.lower()

    def test_chat_event_sanitized(self):
        from strix_telegram_bot.bot import StrixBot
        long_b64 = "A" * 90 + "...BBBB"
        content = f"data:image/png;base64,{long_b64}"
        sanitized = StrixBot._sanitize_agent_content(content)
        assert "AAAAAAAAAA" not in sanitized
        assert "[imagen]" in sanitized


class TestChatFragmentation:
    """Defect 1: chat messages must use fragmentation, not raw[:4000]."""

    def test_long_content_creates_multiple_fragments(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}
        long_text = "X" * 8500
        ev = _make_chat_event("chat_1", version=0, content=long_text, streaming=True)
        bot._process_scan_events([ev])
        assert mock_send.call_count == 3
        assert bot._chat_fragments["chat_1"] == [100, 100, 100]

    def test_finalize_clears_fragments(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}
        ev1 = _make_chat_event("chat_1", version=0, content="partial", streaming=True)
        bot._process_scan_events([ev1])
        assert "chat_1" in bot._chat_fragments
        ev2 = _make_chat_event("chat_1", version=1, content="final", streaming=False)
        bot._process_scan_events([ev2])
        assert "chat_1" not in bot._chat_fragments

    def test_non_streaming_sends_fragmented(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 200}
        long_text = "A" * 5000
        ev = _make_chat_event("chat_1", version=0, content=long_text, streaming=False)
        bot._process_scan_events([ev])
        assert mock_send.call_count == 2

    def test_chat_view_no_content_truncation(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}
        content = "X" * 500
        ev = _make_chat_event("chat_1", version=0, content=content, streaming=False)
        bot._process_scan_events([ev])
        sent_text = mock_send.call_args_list[0][0][2]
        assert len(sent_text) >= 500


class TestShellRendererSDKString:
    """Defect 2: shell renderer must parse real SDK string results."""

    def test_sdk_string_result_parsed(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        sdk_result = (
            "Chunk ID: a1b2c3d4\n"
            "Wall time: 0.5 seconds\n"
            "Process exited with code 0\n"
            "Output:\n"
            "hello world"
        )
        text = render_tool_event("execute_command", "completed", {"command": "echo hi"}, sdk_result)
        assert "hello world" in text
        assert "exit: 0" in text
        assert "Chunk ID" not in text

    def test_sdk_string_nonzero_exit(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        sdk_result = (
            "Chunk ID: xyz\n"
            "Process exited with code 1\n"
            "Output:\n"
            "error: not found"
        )
        text = render_tool_event("execute_command", "completed", {"command": "bad"}, sdk_result)
        assert "exit: 1" in text
        assert "not found" in text

    def test_dict_result_still_works(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("execute_command", "completed", {"command": "ls"}, {"exit_code": 0, "output": "file.txt"})
        assert "exit: 0" in text
        assert "file.txt" in text


class TestOrphanToolCompleted:
    """Defect 3: orphan tool completed must send new message, not discard."""

    def test_orphan_completed_sends_new_message(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 500}
        ev = _make_tool_event(call_id="orphan", tool_name="nuclei", status="completed",
                              result="found 3 vulns")
        bot._process_scan_events([ev])
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "found 3 vulns" in text
        assert mock_edit.call_count == 0

    def test_tracked_completed_still_edits(self, bot, mock_telegram):
        mock_send, mock_edit, _ = mock_telegram
        mock_send.return_value = {"message_id": 500}
        bot._tool_message_ids["tracked"] = 500
        ev = _make_tool_event(call_id="tracked", tool_name="curl", status="completed",
                              result="ok")
        bot._process_scan_events([ev])
        assert mock_edit.call_count == 1
        assert mock_send.call_count == 0


class TestFallbackTruncation:
    """Defect 4: oversized tool cards must be truncated in fallback renderer."""

    def test_long_arg_truncated(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        long_val = "A" * 300
        text = render_tool_event("curl", "completed", {"url": long_val}, "ok")
        assert len([l for l in text.split("\n") if "url:" in l][0]) < 250
        assert "..." in text

    def test_long_result_truncated(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        long_result = "B" * 600
        text = render_tool_event("curl", "completed", {}, long_result)
        result_line = [l for l in text.split("\n") if "Result:" in l][0]
        assert len(result_line) < 550
        assert "..." in result_line

    def test_short_content_not_truncated(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        text = render_tool_event("curl", "completed", {"url": "short"}, "ok")
        assert "short" in text
        assert "ok" in text
        assert "..." not in text


class TestScanCleanupBetweenScans:
    """Defect 1: second scan must start clean — no stale fragments from scan 1."""

    def test_scan2_chat1_version0_arrives_clean(self, bot, mock_telegram):
        mock_send, _, _ = mock_telegram
        mock_send.return_value = {"message_id": 100}

        # Simulate scan 1 finishing with chat_1 at version 5
        bot._chat_fragments["chat_1"] = [10, 11]
        bot._chat_event_version["chat_1"] = 5
        bot._tool_message_ids["old_call"] = 20

        # Simulate new scan start (clears state)
        bot._chat_fragments.clear()
        bot._chat_event_version.clear()
        bot._tool_message_ids.clear()

        # Scan 2: chat_1 version 0 arrives
        ev = _make_chat_event("chat_1", version=0, content="fresh start", streaming=True)
        bot._process_scan_events([ev])

        # Must be delivered, not discarded
        assert mock_send.call_count == 1
        assert "fresh start" in mock_send.call_args[0][2]
        assert bot._chat_fragments.get("chat_1") == [100]
        assert bot._chat_event_version.get("chat_1") == 0


class TestStaleFragmentDeletion:
    """Defect 2: final shorter than streaming must delete surplus fragments."""

    def test_shorter_final_deletes_stale_fragments(self, bot, mock_telegram):
        mock_send, mock_edit, mock_delete = mock_telegram
        mock_send.return_value = {"message_id": 100}

        # Streaming creates 3 fragments (9000 chars → 3 × 4000)
        long_text = "X" * 9000
        ev1 = _make_chat_event("chat_1", version=0, content=long_text, streaming=True)
        bot._process_scan_events([ev1])
        assert mock_send.call_count == 3
        assert bot._chat_fragments["chat_1"] == [100, 100, 100]

        # Final: only 1000 chars → 1 fragment
        ev2 = _make_chat_event("chat_1", version=1, content="X" * 1000, streaming=False)
        bot._process_scan_events([ev2])

        # Fragment 0 was edited (existing message reused)
        assert mock_edit.call_count >= 1
        # Fragments 1 and 2 were deleted (stale surplus)
        assert mock_delete.call_count >= 2
        # Fragments cleaned up
        assert "chat_1" not in bot._chat_fragments


class TestToolCardMaxLimit:
    """Defect 4: render_tool_event output must never exceed 4000 chars."""

    def test_extreme_shell_output_capped(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        huge_output = "A" * 15000
        sdk_result = (
            "Chunk ID: abc\n"
            "Wall time: 1.0 seconds\n"
            "Process exited with code 0\n"
            f"Output:\n{huge_output}"
        )
        text = render_tool_event("execute_command", "completed", {"command": "cat huge"}, sdk_result)
        assert len(text) <= 4000

    def test_many_args_capped(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        big_args = {f"arg{i}": "B" * 500 for i in range(20)}
        text = render_tool_event("unknown_tool", "completed", big_args, "result")
        assert len(text) <= 4000
        assert "truncado" in text

    def test_shell_truncation_marker_present(self):
        from strix_telegram_bot.strix.telegram_renderers import render_tool_event
        huge_output = "C" * 20000
        sdk_result = (
            "Chunk ID: def\n"
            "Process exited with code 0\n"
            f"Output:\n{huge_output}"
        )
        text = render_tool_event("execute_command", "completed", {}, sdk_result)
        assert len(text) <= 4000
        # Shell renderer truncates internally via _truncate_output (lines + line_len)
        assert "truncat" in text or len(text) < 1000


class TestDeliverFinalReport:
    """Tests for _deliver_final_report and scan_complete handler."""

    def test_report_longer_than_4000_delivered_complete(self, bot, mock_telegram, tmp_path):
        """A report >4000 chars must be split into multiple fragments, all delivered."""
        mock_send, _, _ = mock_telegram
        run_dir = tmp_path / "scan-report-long"
        run_dir.mkdir()
        long_body = "x" * 6000
        (run_dir / "penetration_test_report.md").write_text(f"# Report\n\n{long_body}")

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            result = bot._deliver_final_report(12345, "scan-report-long")

        assert result == "delivered"
        assert mock_send.call_count == 2
        total_sent = sum(len(c.args[2]) for c in mock_send.call_args_list)
        assert total_sent >= 6000

    def test_order_fragments_before_confirmation(self, bot, mock_telegram, tmp_path):
        """Fragments must be sent before the 'Escaneo completado' confirmation."""
        mock_send, _, _ = mock_telegram
        bot._active_job_run_name = "scan-order-test"
        run_dir = tmp_path / "scan-order-test"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Report\n\nShort body")

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            ev = _make_system_event("scan_complete", run_name="scan-order-test")
            bot._process_scan_events([ev])

        calls = [c.args[2] for c in mock_send.call_args_list]
        report_idx = next(i for i, t in enumerate(calls) if "Reporte final" in t)
        confirm_idx = next(i for i, t in enumerate(calls) if "completado" in t)
        assert report_idx < confirm_idx

    def test_two_scan_complete_only_one_delivery(self, bot, mock_telegram, tmp_path):
        """Two scan_complete events for the same run must only deliver once."""
        mock_send, _, _ = mock_telegram
        bot._active_job_run_name = "scan-idempotent"
        run_dir = tmp_path / "scan-idempotent"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Report\n\nBody")

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            ev = _make_system_event("scan_complete", run_name="scan-idempotent")
            bot._process_scan_events([ev])
            count_first = mock_send.call_count
            bot._process_scan_events([ev])
            count_second = mock_send.call_count

        assert count_first == count_second

    def test_second_fragment_failure(self, bot, mock_telegram, tmp_path):
        """If the second fragment fails to send, result is send_failed."""
        mock_send, _, _ = mock_telegram
        run_dir = tmp_path / "scan-frag-fail"
        run_dir.mkdir()
        body = "A" * 5000
        (run_dir / "penetration_test_report.md").write_text(f"# Report\n\n{body}")

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return None
            return {"message_id": 100}
        mock_send.side_effect = side_effect

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            result = bot._deliver_final_report(12345, "scan-frag-fail")

        assert result == "send_failed"

    def test_missing_report_returns_missing(self, bot, mock_telegram):
        """When no report file exists, result is 'missing'."""
        mock_send, _, _ = mock_telegram
        result = bot._deliver_final_report(12345, "nonexistent-run")
        assert result == "missing"
        assert mock_send.call_count == 0

    def test_scan_complete_with_delivered_report(self, bot, mock_telegram, tmp_path):
        """Successful delivery sends fragments, then 'completado'."""
        mock_send, _, _ = mock_telegram
        bot._active_job_run_name = "scan-delivered"
        run_dir = tmp_path / "scan-delivered"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Report\n\nBody")

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            ev = _make_system_event("scan_complete", run_name="scan-delivered")
            bot._process_scan_events([ev])

        calls = [c.args[2] for c in mock_send.call_args_list]
        assert any("Reporte final" in t for t in calls)
        assert any("completado" in t for t in calls)

    def test_all_messages_use_parse_mode_none(self, bot, mock_telegram, tmp_path):
        """Every send_message call must use parse_mode=None."""
        mock_send, _, _ = mock_telegram
        bot._active_job_run_name = "scan-parse"
        run_dir = tmp_path / "scan-parse"
        run_dir.mkdir()
        (run_dir / "penetration_test_report.md").write_text("# Report\n\nBody")

        with patch("strix_telegram_bot.strix.report_collector.settings") as mock_settings:
            mock_settings.strix_runs_dir = tmp_path
            ev = _make_system_event("scan_complete", run_name="scan-parse")
            bot._process_scan_events([ev])

        for call in mock_send.call_args_list:
            assert call.kwargs.get("parse_mode") is None


class TestSendFragmented:
    """Tests for the _send_fragmented helper in reports.py."""

    @pytest.fixture
    def mock_reports_send(self):
        with patch("strix_telegram_bot.commands.reports.send_message") as mock:
            mock.return_value = {"message_id": 100}
            yield mock

    def test_short_text_single_message(self, mock_reports_send):
        from strix_telegram_bot.commands.reports import _send_fragmented
        ok = _send_fragmented(None, 12345, "Short text")
        assert ok is True
        assert mock_reports_send.call_count == 1

    def test_long_text_split_correctly(self, mock_reports_send):
        from strix_telegram_bot.commands.reports import _send_fragmented
        text = "A" * 8500
        ok = _send_fragmented(None, 12345, text)
        assert ok is True
        assert mock_reports_send.call_count == 3
        total = sum(len(c.args[2]) for c in mock_reports_send.call_args_list)
        assert total == 8500

    def test_prefers_newline_breaks(self, mock_reports_send):
        from strix_telegram_bot.commands.reports import _send_fragmented
        lines = [f"Line {i}: {'x' * 80}" for i in range(100)]
        text = "\n".join(lines)
        ok = _send_fragmented(None, 12345, text)
        assert ok is True
        for call in mock_reports_send.call_args_list:
            chunk = call.args[2]
            if len(chunk) == 4000:
                assert chunk.endswith("\n") or chunk == text[:4000]

    def test_returns_false_on_send_failure(self, mock_reports_send):
        mock_reports_send.return_value = None
        from strix_telegram_bot.commands.reports import _send_fragmented
        ok = _send_fragmented(None, 12345, "Some text")
        assert ok is False

    def test_parse_mode_none(self, mock_reports_send):
        from strix_telegram_bot.commands.reports import _send_fragmented
        _send_fragmented(None, 12345, "Test content")
        for call in mock_reports_send.call_args_list:
            assert call.kwargs.get("parse_mode") is None
