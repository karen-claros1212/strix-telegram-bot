from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import FocusPreset, MenuState, ProfileType, ScanMode, ScopeMode, TargetType


_CALLBACK_SEP = ":"
_MAX_CALLBACK_LEN = 64


def _cb(*parts: str) -> str:
    raw = _CALLBACK_SEP.join(parts)
    return raw[:_MAX_CALLBACK_LEN]


def _btn(text: str, callback_data: str) -> dict:
    return {"text": text, "callback_data": callback_data}


def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
    return {"inline_keyboard": buttons}


def main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("New Pentest", _cb("menu", "new_pentest")),
            _btn("Chat", _cb("menu", "chat")),
        ],
        [
            _btn("Active Jobs", _cb("menu", "jobs")),
            _btn("Reports", _cb("menu", "reports")),
        ],
        [
            _btn("Evidence", _cb("menu", "evidence")),
            _btn("Tools", _cb("menu", "tools")),
        ],
        [
            _btn("Caido Proxy", _cb("menu", "caido")),
            _btn("Health", _cb("menu", "health")),
        ],
        [
            _btn("Config", _cb("menu", "config")),
            _btn("Help", _cb("menu", "help")),
        ],
    ])


def jobs_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Active", _cb("job", "active")),
            _btn("Completed", _cb("job", "completed")),
        ],
        [
            _btn("Failed", _cb("job", "failed")),
            _btn("Stopped", _cb("job", "stopped")),
        ],
        [
            _btn("Back to Menu", _cb("menu", "main")),
        ],
    ])


def reports_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Latest Report", _cb("report", "latest")),
            _btn("History", _cb("report", "history")),
        ],
        [
            _btn("Executive Summary", _cb("report", "summary")),
            _btn("Evidence", _cb("report", "evidence")),
        ],
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("CSV", _cb("report", "csv")),
            _btn("JSON", _cb("report", "json")),
        ],
        [
            _btn("Cleanup Old", _cb("report", "cleanup")),
            _btn("Back to Menu", _cb("menu", "main")),
        ],
    ])


def caido_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Detect Caido", _cb("caido", "detect")),
            _btn("Status", _cb("caido", "status")),
        ],
        [
            _btn("Artifacts", _cb("caido", "artifacts")),
            _btn("Instructions", _cb("caido", "instructions")),
        ],
        [
            _btn("Back to Menu", _cb("menu", "main")),
        ],
    ])


def target_type_selector() -> dict:
    return build_inline_keyboard([
        [_btn("URL / Domain", _cb("target", "url"))],
        [_btn("GitHub Repo", _cb("target", "github"))],
        [_btn("Local Code", _cb("target", "local"))],
        [_btn("File Attachment", _cb("target", "attachment"))],
        [_btn("Multi-target", _cb("target", "multi"))],
        [_btn("Back", _cb("menu", "main"))],
    ])


def depth_selector() -> dict:
    return build_inline_keyboard([
        [
            _btn("Quick", _cb("depth", "quick")),
            _btn("Standard", _cb("depth", "standard")),
            _btn("Deep", _cb("depth", "deep")),
        ],
        [_btn("Continue", _cb("depth", "confirm")),
         _btn("Back", _cb("menu", "new_pentest"))],
    ])


def profile_selector() -> dict:
    return build_inline_keyboard([
        [
            _btn("Interactive / TUI", _cb("profile", "interactive")),
            _btn("Headless / CI", _cb("profile", "headless")),
        ],
        [_btn("Back", _cb("menu", "new_pentest"))],
    ])


def scope_mode_selector() -> dict:
    return build_inline_keyboard([
        [
            _btn("Auto", _cb("scope", "auto")),
            _btn("Diff", _cb("scope", "diff")),
            _btn("Full", _cb("scope", "full")),
        ],
        [
            _btn("Diff Base", _cb("scope", "diff_base")),
        ],
        [_btn("Skip Scope Config", _cb("scope", "done"))],
    ])


def focus_presets() -> dict:
    return build_inline_keyboard([
        [_btn("Business Logic / IDOR", _cb("focus", "business_logic"))],
        [_btn("Auth / Session / JWT", _cb("focus", "auth_jwt"))],
        [_btn("SQL / NoSQL / SSTI", _cb("focus", "sql"))],
        [_btn("XSS / CSRF / DOM", _cb("focus", "xss"))],
        [_btn("SSRF / XXE / Deserialization", _cb("focus", "ssrf"))],
        [_btn("Kubernetes / Infra", _cb("focus", "kubernetes"))],
        [_btn("Secrets / Supply chain", _cb("focus", "secrets"))],
        [_btn("Custom", _cb("focus", "custom"))],
        [_btn("Skip (no instruction)", _cb("focus", "skip"))],
    ])


def evidence_list_menu(artifacts: list[dict]) -> dict:
    rows = []
    for a in artifacts[:10]:
        label = f"{a.get('type', '?')} - {a.get('id', '?')[:40]}"
        rows.append([_btn(label, _cb("evidence", a["id"]))])
    rows.append([_btn("Back", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def evidence_detail_menu(artifact_id: str) -> dict:
    return build_inline_keyboard([
        [
            _btn("Preview (redacted)", _cb("evidence", f"preview:{artifact_id}")),
        ],
        [
            _btn("Send RAW", _cb("evidence", f"raw:{artifact_id}")),
            _btn("Send Redacted", _cb("evidence", f"redacted:{artifact_id}")),
        ],
        [_btn("Back to Evidence", _cb("menu", "evidence_list"))],
    ])


def tools_panel(active_tools: list[dict]) -> dict:
    rows = []
    for tool in active_tools:
        name = tool.get("name", "unknown")
        rows.append([_btn(name, _cb("tools", name.lower()))])
    if not active_tools:
        rows.append([_btn("No tools active", _cb("tools", "none"))])
    rows.append([_btn("Back to Menu", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def job_panel(running: bool = False) -> dict:
    row = [_btn("Chat", _cb("job", "chat"))]
    if running:
        row.append(_btn("STOP", _cb("job", "stop")))
    row.append(_btn("Status", _cb("job", "status")))
    return build_inline_keyboard([
        row,
        [
            _btn("Caido", _cb("job", "caido")),
            _btn("Reports", _cb("job", "reports")),
            _btn("Back to Menu", _cb("menu", "main")),
        ],
    ])


def active_jobs_list(job_names: list[str]) -> dict:
    rows = []
    for name in job_names[:8]:
        rows.append([_btn(name, _cb("job_detail", name))])
    rows.append([_btn("Back", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def reports_list(report_names: list[str]) -> dict:
    rows = []
    for name in report_names[:8]:
        rows.append([_btn(name, _cb("report", name))])
    rows.append([_btn("Back", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def report_detail_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("CSV", _cb("report", "csv")),
        ],
        [
            _btn("Evidence", _cb("report", "evidence")),
            _btn("Download", _cb("report", "download")),
        ],
        [_btn("Back to Reports", _cb("report", "list"))],
    ])


def config_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Default Mode", _cb("config", "mode"))],
        [_btn("Scope Mode", _cb("config", "scope"))],
        [_btn("LLM Model", _cb("config", "llm"))],
        [_btn("Allowed Users", _cb("config", "users"))],
        [_btn("Back", _cb("menu", "main"))],
    ])


def back_to_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Back to Menu", _cb("menu", "main"))],
    ])


def parse_callback(data: str) -> tuple[str, ...]:
    parts = data.split(_CALLBACK_SEP)
    return tuple(parts)


def menu_from_state(state: MenuState, **kwargs) -> dict:
    mapping = {
        MenuState.MAIN: main_menu,
        MenuState.NEW_PENTEST_TARGET: target_type_selector,
        MenuState.NEW_PENTEST_DEPTH: depth_selector,
        MenuState.NEW_PENTEST_PROFILE: profile_selector,
        MenuState.NEW_PENTEST_SCOPE: scope_mode_selector,
        MenuState.NEW_PENTEST_FOCUS: focus_presets,
        MenuState.CONFIG: config_menu,
        MenuState.TOOLS: lambda: tools_panel(kwargs.get("active_tools", [])),
    }
    builder = mapping.get(state, main_menu)
    return builder()
