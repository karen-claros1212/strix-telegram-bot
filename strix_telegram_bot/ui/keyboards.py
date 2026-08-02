from __future__ import annotations

import hashlib
import logging

from strix_telegram_bot.models import MenuState

logger = logging.getLogger(__name__)

_CALLBACK_SEP = ":"
_MAX_CALLBACK_LEN = 64


def _cb(*parts: str) -> str:
    raw = _CALLBACK_SEP.join(parts)
    if len(raw) <= _MAX_CALLBACK_LEN:
        return raw
    # Truncate long parts with a hash suffix for uniqueness
    logger.warning("Callback too long (%d chars): %s... — truncating", len(raw), raw[:40])
    shortened = list(parts)
    for i in range(len(shortened) - 1, -1, -1):
        if len(shortened[i]) > 8:
            h = hashlib.md5(shortened[i].encode()).hexdigest()[:6]
            shortened[i] = shortened[i][:4] + h
        candidate = _CALLBACK_SEP.join(shortened)
        if len(candidate) <= _MAX_CALLBACK_LEN:
            return candidate
    return raw[_MAX_CALLBACK_LEN - 10:] + raw[-10:]


def _btn(text: str, callback_data: str) -> dict:
    return {"text": text, "callback_data": callback_data}


def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
    return {"inline_keyboard": buttons}


def main_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Escanear", _cb("menu", "scan"))],
    ])


def scan_mode_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Quick", _cb("menu", "mode", "quick"))],
        [_btn("Standard", _cb("menu", "mode", "standard"))],
        [_btn("Deep", _cb("menu", "mode", "deep"))],
        [_btn("Volver", _cb("menu", "main"))],
    ])


def job_panel(running: bool = False, agent_count: int = 0) -> dict:
    buttons = []
    if running:
        buttons.append(_btn("Detener", _cb("job", "stop")))
    rows = [buttons]
    if running:
        rows.append([_btn("Chat", _cb("job", "chat")),
                     _btn("Arbol", _cb("job", "tree"))])
        rows.append([_btn("Vulns", _cb("job", "vulns"))])
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


def reports_main_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Último reporte", _cb("report", "latest"))],
        [_btn("Historial", _cb("report", "history"))],
        [_btn("Resumen ejecutivo", _cb("report", "summary"))],
        [_btn("Evidencia", _cb("report", "evidence"))],
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("CSV", _cb("report", "csv")),
            _btn("JSON", _cb("report", "json")),
        ],
        [_btn("Limpiar viejos", _cb("report", "cleanup"))],
        [_btn("Volver", _cb("menu", "main"))],
    ])


def report_detail_menu(run_name: str) -> dict:
    return build_inline_keyboard([
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("Descargar MD", _cb("report", "download_md", run_name)),
        ],
        [
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
