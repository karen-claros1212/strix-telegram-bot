from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import MenuState, ScanMode, TargetType


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
        MenuState.CONFIG: config_menu,
    }
    builder = mapping.get(state, main_menu)
    return builder()
