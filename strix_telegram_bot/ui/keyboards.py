from __future__ import annotations

from strix_telegram_bot.models import MenuState


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
        [_btn("Escanear", _cb("menu", "scan"))],
    ])


def job_panel(running: bool = False, agent_count: int = 0) -> dict:
    buttons = []
    if running:
        buttons.append(_btn("Detener", _cb("job", "stop")))
    buttons.append(_btn("Estado", _cb("job", "status")))
    rows = [buttons]
    if agent_count > 1:
        rows.append([_btn("Agentes", _cb("job", "agents"))])
    return build_inline_keyboard(rows)


def agent_selector(agents: list[dict]) -> dict:
    rows = []
    for a in agents:
        label = a.get("name", a["id"])[:40]
        status_icon = _status_icon(a.get("status", ""))
        rows.append([_btn(f"{status_icon} {label}", _cb("agent", a["id"]))])
    rows.append([_btn("Volver", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def _status_icon(status: str) -> str:
    return {
        "running": "▶",
        "waiting": "⏳",
        "completed": "✅",
        "stopped": "⏹",
        "failed": "❌",
    }.get(status, "?")


def back_to_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Volver al menú", _cb("menu", "main"))],
    ])


def config_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Volver", _cb("menu", "main"))],
    ])


def reports_list(report_names: list[str]) -> dict:
    rows = []
    for name in report_names[:8]:
        rows.append([_btn(name, _cb("report", name))])
    rows.append([_btn("Volver", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def report_detail_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("CSV", _cb("report", "csv")),
        ],
        [
            _btn("Evidencia", _cb("report", "evidence")),
        ],
        [_btn("Volver a reportes", _cb("report", "list"))],
    ])


def parse_callback(data: str) -> tuple[str, ...]:
    parts = data.split(_CALLBACK_SEP)
    return tuple(parts)


def menu_from_state(state: MenuState, **kwargs) -> dict:
    mapping = {
        MenuState.MAIN: main_menu,
        MenuState.WAITING_FOR_TARGETS: back_to_menu,
    }
    builder = mapping.get(state, main_menu)
    return builder()
