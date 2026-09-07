"""Compatibility contracts: Radamanthys vs strix-agent 1.6.2.

Guards the upstream Strix contracts the Telegram mirror depends on, so a
future Strix bump that changes wait/termination/coordination semantics
breaks here before it breaks the bot in production.

Contract surface covered:
- AgentCoordinator.send() to terminal non-resumable agents (1.6.2: False)
- wait_for_agents early-return wait_outcome=no_active_agents (1.6.2, non-interactive)
- respond_to_user / wait_kind=user path
- agent states completed/failed/stopped (+ TERMINAL_STATUSES)
- agent_finish output includes filed_report_ids (1.6.2)
- ReportState global accessors
- run_dir_for, run_strix_scan, GoTuiRuntime, TuiLiveView,
  send_user_message_to_agent, prepare_run, build_targets_info,
  DEFAULT_MAX_TURNS
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path


def _tool_ctx(inner: dict, tool_name: str, args: dict):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=inner,
        tool_name=tool_name,
        tool_call_id="compat-test-call",
        tool_arguments=json.dumps(args),
        run_config=None,
    )


# ---------------------------------------------------------------- imports
def test_import_contract_all_bot_symbols():
    """Every strix symbol Radamanthys imports must resolve in 1.6.2."""
    from strix.config.settings import DEFAULT_MAX_TURNS  # noqa: F401
    from strix.core.agents import AgentCoordinator, coordinator_from_context  # noqa: F401
    from strix.core.paths import run_dir_for  # noqa: F401
    from strix.core.runner import run_strix_scan  # noqa: F401
    from strix.interface.scan_setup import build_targets_info, prepare_run  # noqa: F401
    from strix.interface.tui.backend.messages import send_user_message_to_agent  # noqa: F401
    from strix.interface.tui.live_view import TuiLiveView  # noqa: F401
    from strix.interface.tui.runtime import GoTuiRuntime  # noqa: F401
    from strix.interface.utils import (  # noqa: F401
        assign_workspace_subdirs,
        build_diff_scope_instruction,
        clone_repository,
        collect_local_sources,
        infer_target_type,
        resolve_diff_scope_context,
    )
    from strix.report.state import (  # noqa: F401
        ReportState,
        get_global_report_state,
        set_global_report_state,
    )
    from strix.runtime import session_manager  # noqa: F401
    from strix.tools.agents_graph.tools import wait_for_agents  # noqa: F401
    from strix.tools.respond.tool import respond_to_user  # noqa: F401


# ---------------------------------------------------------------- states
def test_states_completed_failed_stopped():
    from strix.core.agents import TERMINAL_STATUSES, Status

    assert "completed" in Status.__args__
    assert "failed" in Status.__args__
    assert "stopped" in Status.__args__
    assert "running" in Status.__args__
    assert "waiting" in Status.__args__
    assert TERMINAL_STATUSES == frozenset({"completed", "stopped", "crashed", "failed"})


def test_wait_kind_user_agents():
    from strix.core.agents import WaitKind

    assert "user" in WaitKind.__args__
    assert "agents" in WaitKind.__args__


# ---------------------------------------------------------------- coordinator
def test_send_to_terminal_agent_returns_false():
    """1.6.2: send() to a terminal non-resumable agent returns False (no wake)."""
    from strix.core.agents import AgentCoordinator

    async def run():
        c = AgentCoordinator()
        await c.register("parent", "parent", None)
        await c.register("child", "child", "parent")
        await c.attach_runtime("child", resumable=False)
        await c.set_status("child", "completed")
        return await c.send("child", {"from": "parent", "text": "ping"})

    result = asyncio.new_event_loop().run_until_complete(run())
    assert result is False, "send() to terminal non-resumable agent must return False in 1.6.2"


def test_send_to_waiting_agent_returns_true():
    from strix.core.agents import AgentCoordinator

    async def run():
        c = AgentCoordinator()
        await c.register("parent", "parent", None)
        await c.register("child", "child", "parent")
        await c.park_waiting("child", wait_kind="agents")
        return await c.send("child", {"from": "parent", "text": "ping"})

    result = asyncio.new_event_loop().run_until_complete(run())
    assert result is True, "send() to a waiting agent must return True"


def test_wait_kind_of_user():
    """respond_to_user path: agent parked with wait_kind=user."""
    from strix.core.agents import AgentCoordinator

    async def run():
        c = AgentCoordinator()
        await c.register("a1", "a1", None)
        await c.park_waiting("a1", wait_kind="user")
        return await c.wait_kind_of("a1")

    kind = asyncio.new_event_loop().run_until_complete(run())
    assert kind == "user"


def test_wait_for_agents_no_active_agents():
    """1.6.2 NEW: non-interactive wait_for_agents returns wait_outcome=no_active_agents."""
    from strix.core.agents import AgentCoordinator
    from strix.tools.agents_graph.tools import wait_for_agents

    async def run():
        c = AgentCoordinator()
        await c.register("me", "me", None)
        await c.set_status("me", "running")
        ctx = _tool_ctx(
            {"coordinator": c, "agent_id": "me", "interactive": False},
            "wait_for_agents",
            {"reason": "compat-test", "timeout_seconds": 5},
        )
        out = await wait_for_agents.on_invoke_tool(
            ctx, json.dumps({"reason": "compat-test", "timeout_seconds": 5})
        )
        return json.loads(out)

    out = asyncio.new_event_loop().run_until_complete(run())
    assert out["success"] is True
    assert out["wait_outcome"] == "no_active_agents", out


def test_wait_for_agents_waits_when_active_agent_exists():
    """When another agent is running, wait_for_agents must park (not no_active_agents)."""
    from strix.core.agents import AgentCoordinator
    from strix.tools.agents_graph.tools import wait_for_agents

    async def run():
        c = AgentCoordinator()
        await c.register("me", "me", None)
        await c.register("sibling", "sibling", None)
        await c.set_status("me", "running")
        await c.set_status("sibling", "running")
        ctx = _tool_ctx(
            {"coordinator": c, "agent_id": "me", "interactive": False},
            "wait_for_agents",
            {"reason": "compat-test", "timeout_seconds": 10},
        )

        async def release_after_park():
            await asyncio.sleep(0.5)
            await c.send("me", {"from": "sibling", "text": "done"})

        task = asyncio.ensure_future(release_after_park())
        out = await wait_for_agents.on_invoke_tool(
            ctx, json.dumps({"reason": "compat-test", "timeout_seconds": 10})
        )
        await task
        return json.loads(out)

    out = asyncio.new_event_loop().run_until_complete(run())
    assert out["success"] is True
    assert out.get("wait_outcome") != "no_active_agents", out


def test_agent_finish_includes_filed_report_ids():
    """1.6.2 NEW: agent_finish completion report includes filed_report_ids of filed vulns."""
    from strix.core.agents import AgentCoordinator
    from strix.report.state import ReportState, set_global_report_state
    from strix.tools.agents_graph.tools import agent_finish

    async def run():
        rs = ReportState(run_name="compat-test-run")
        set_global_report_state(rs)
        report_id = rs.add_vulnerability_report(
            title="Test XSS", severity="high", agent_id="me", agent_name="me"
        )
        c = AgentCoordinator()
        await c.register("root", "root", None)
        await c.register("me", "me", "root")
        await c.set_status("me", "running")
        ctx = _tool_ctx(
            {"coordinator": c, "agent_id": "me", "parent_id": "root"},
            "agent_finish",
            {"result_summary": "done"},
        )
        out = await agent_finish.on_invoke_tool(ctx, json.dumps({"result_summary": "done"}))
        return json.loads(out), report_id

    out, report_id = asyncio.new_event_loop().run_until_complete(run())
    assert "filed_report_ids" in out, f"missing in agent_finish output: {out.keys()}"
    assert report_id in out["filed_report_ids"], out


# ---------------------------------------------------------------- report state
def test_report_state_global_accessors():
    from strix.report.state import get_global_report_state, set_global_report_state

    set_global_report_state(None)
    assert get_global_report_state() is None


# ---------------------------------------------------------------- paths / runner / tui
def test_run_dir_for_behavior():
    from strix.core.paths import run_dir_for

    p = run_dir_for("my-run", cwd=Path("/tmp"))
    assert p == Path("/tmp/strix_runs/my-run"), p


def test_run_strix_scan_signature():
    from strix.core.runner import run_strix_scan

    assert inspect.iscoroutinefunction(run_strix_scan)


def test_go_tui_runtime_and_live_view():
    from strix.interface.tui.live_view import TuiLiveView
    from strix.interface.tui.runtime import GoTuiRuntime

    assert inspect.isclass(GoTuiRuntime)
    assert inspect.isclass(TuiLiveView)


def test_send_user_message_to_agent():
    from strix.interface.tui.backend.messages import send_user_message_to_agent

    assert callable(send_user_message_to_agent)


def test_scan_setup_signatures():
    from strix.interface.scan_setup import build_targets_info, prepare_run

    assert callable(build_targets_info)
    assert callable(prepare_run)


def test_default_max_turns():
    from strix.config.settings import DEFAULT_MAX_TURNS

    assert DEFAULT_MAX_TURNS == 500


# -------------------------- premium mirror contracts (A-F) --------------------------

def test_a_prepare_and_start_is_async_lifecycle():
    """Workstream A: the bridge delegates the whole start to GoTuiRuntime.
    prepare_and_start must exist and be a coroutine (preflight -> prepare_run
    -> init_run_state -> start_scan all live inside it)."""
    from strix.interface.tui.runtime import GoTuiRuntime

    assert hasattr(GoTuiRuntime, "prepare_and_start")
    assert inspect.iscoroutinefunction(GoTuiRuntime.prepare_and_start)
    # The pieces it orchestrates must also exist on the runtime.
    assert hasattr(GoTuiRuntime, "init_run_state")
    assert hasattr(GoTuiRuntime, "start_scan")
    assert inspect.iscoroutinefunction(GoTuiRuntime.quit)


def test_c_atomic_write_json_atomicity(tmp_path):
    """Workstream C: the shared primitive writes atomically (temp sibling +
    fsync + os.replace) and never leaves a partial file or a stray temp."""
    import json as _json

    from strix_telegram_bot.persistence import atomic_write_json

    target = tmp_path / "sub" / "state.json"
    atomic_write_json(target, {"a": 1, "b": [1, 2, 3]})
    assert target.is_file()
    assert _json.loads(target.read_text()) == {"a": 1, "b": [1, 2, 3]}
    # No leftover temp files anywhere under the tree.
    leftovers = [p for p in tmp_path.rglob("*.tmp*") if p.is_file()]
    assert leftovers == [], f"stray temp files: {leftovers}"
    # Overwrite is also atomic and correct.
    atomic_write_json(target, {"a": 99})
    assert _json.loads(target.read_text()) == {"a": 99}


def test_d_mcp_roster_contract():
    """Workstream D: the engine reports the MCP roster through
    capture_mcp_status -> controller.set_mcp_connections -> mcp_connections,
    normalizing each entry to {name, tool_count, dead}."""
    from strix.interface.tui.backend.controller import TuiController

    ctrl = TuiController.__new__(TuiController)
    ctrl.mcp_connections = []
    ctrl._on_change = None  # notify_changed() is a no-op without a subscriber
    ctrl.set_mcp_connections([
        {"name": "http", "tool_count": 12, "dead": False},
        {"name": "fs", "tool_count": 4, "dead": True},
        {"name": ""},  # dropped (no name)
        "not-a-dict",  # dropped
    ])
    assert ctrl.mcp_connections == [
        {"name": "http", "tool_count": 12, "dead": False},
        {"name": "fs", "tool_count": 4, "dead": True},
    ], ctrl.mcp_connections


def test_e_read_workspace_files_contract(tmp_path):
    """Workstream E: read_workspace_files turns {source_path, workspace_path}
    into engine extra_files entries {workspace_path, content} (bytes)."""
    from strix.interface.utils import read_workspace_files

    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello world")
    entries = read_workspace_files([
        {"source_path": str(src), "workspace_path": "notes.txt"},
    ])
    assert len(entries) == 1
    assert entries[0]["workspace_path"] == "notes.txt"
    assert entries[0]["content"] == b"hello world"
    # None / empty input yields no entries.
    assert read_workspace_files(None) == []
    assert read_workspace_files([]) == []


def test_f_write_sarif_contract(tmp_path):
    """Workstream F: the official SARIF emitter writes findings.sarif (2.1.0)
    into the run dir — the bot reads it, never regenerates it."""
    from strix.report.sarif import write_sarif

    vulns = [
        {
            "id": "vuln-1",
            "title": "SQLi",
            "severity": "high",
            "description": "desc",
            "cwe": "CWE-89",
            "cvss": 7.5,
            "endpoint": "/api/x",
            "method": "GET",
            "parameter": "q",
            "payload": "1' OR 1=1",
            "evidence": "ev",
            "recommendation": "fix",
        }
    ]
    write_sarif(tmp_path, vulns, tool_version="1.6.2")
    sarif = tmp_path / "findings.sarif"
    assert sarif.is_file(), "findings.sarif must be produced by the official emitter"
    import json as _json

    doc = _json.loads(sarif.read_text())
    assert doc.get("version", "").startswith("2.1"), doc.get("version")
    # The finding must be reflected in the SARIF results.
    results = doc.get("runs", [{}])[0].get("results", [])
    assert any("SQLi" in str(r) for r in results), "vulnerability missing from SARIF"
