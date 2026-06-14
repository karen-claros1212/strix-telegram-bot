"""Teclados en español para la UI del bot."""

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
        [_btn("Escanear", _cb("menu", "new_pentest"))],
    ])


def jobs_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Activos", _cb("job", "active")),
            _btn("Completados", _cb("job", "completed")),
        ],
        [
            _btn("Fallidos", _cb("job", "failed")),
            _btn("Detenidos", _cb("job", "stopped")),
        ],
        [
            _btn("Volver", _cb("menu", "main")),
        ],
    ])


def reports_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Último reporte", _cb("report", "latest")),
            _btn("Historial", _cb("report", "history")),
        ],
        [
            _btn("Resumen ejecutivo", _cb("report", "summary")),
            _btn("Evidencia", _cb("report", "evidence")),
        ],
        [
            _btn("Markdown", _cb("report", "markdown")),
            _btn("CSV", _cb("report", "csv")),
            _btn("JSON", _cb("report", "json")),
        ],
        [
            _btn("Limpiar viejos", _cb("report", "cleanup")),
            _btn("Volver", _cb("menu", "main")),
        ],
    ])


def caido_main_menu() -> dict:
    return build_inline_keyboard([
        [
            _btn("Detectar Caido", _cb("caido", "detect")),
            _btn("Estado", _cb("caido", "status")),
        ],
        [
            _btn("Archivos", _cb("caido", "artifacts")),
            _btn("Instrucciones", _cb("caido", "instructions")),
        ],
        [
            _btn("Volver", _cb("menu", "main")),
        ],
    ])


def target_type_selector() -> dict:
    return build_inline_keyboard([
        [_btn("URL / Dominio", _cb("target", "url"))],
        [_btn("Repo GitHub", _cb("target", "github"))],
        [_btn("Código local", _cb("target", "local"))],
        [_btn("Archivo adjunto", _cb("target", "attachment"))],
        [_btn("Multi-objetivo", _cb("target", "multi"))],
        [_btn("Volver", _cb("menu", "main"))],
    ])


def depth_selector() -> dict:
    return build_inline_keyboard([
        [
            _btn("Rápido", _cb("depth", "quick")),
            _btn("Estándar", _cb("depth", "standard")),
            _btn("Profundo", _cb("depth", "deep")),
        ],
        [_btn("Continuar", _cb("depth", "confirm")),
         _btn("Volver", _cb("menu", "new_pentest"))],
    ])


def profile_selector() -> dict:
    return build_inline_keyboard([
        [
            _btn("Interactivo / TUI", _cb("profile", "interactive")),
            _btn("Headless / CI", _cb("profile", "headless")),
        ],
        [_btn("Volver", _cb("menu", "new_pentest"))],
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
        [_btn("Saltar config", _cb("scope", "done"))],
    ])


def focus_presets() -> dict:
    return build_inline_keyboard([
        [_btn("Lógica de negocio / IDOR", _cb("focus", "business_logic"))],
        [_btn("Auth / Sesión / JWT", _cb("focus", "auth_jwt"))],
        [_btn("SQL / NoSQL / SSTI", _cb("focus", "sql"))],
        [_btn("XSS / CSRF / DOM", _cb("focus", "xss"))],
        [_btn("SSRF / XXE / Deserialización", _cb("focus", "ssrf"))],
        [_btn("Kubernetes / Infra", _cb("focus", "kubernetes"))],
        [_btn("Secretos / Supply chain", _cb("focus", "secrets"))],
        [_btn("Personalizado", _cb("focus", "custom"))],
        [_btn("Saltar (sin instrucción)", _cb("focus", "skip"))],
    ])


def evidence_list_menu(artifacts: list[dict]) -> dict:
    rows = []
    for a in artifacts[:10]:
        label = f"{a.get('type', '?')} - {a.get('id', '?')[:40]}"
        rows.append([_btn(label, _cb("evidence", a["id"]))])
    rows.append([_btn("Volver", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


def evidence_detail_menu(artifact_id: str) -> dict:
    return build_inline_keyboard([
        [
            _btn("Vista previa (censurada)", _cb("evidence", f"preview:{artifact_id}")),
        ],
        [
            _btn("Enviar CRUDO", _cb("evidence", f"raw:{artifact_id}")),
            _btn("Enviar censurado", _cb("evidence", f"redacted:{artifact_id}")),
        ],
        [_btn("Volver a evidencia", _cb("menu", "evidence_list"))],
    ])


def tools_panel(active_tools: list[dict]) -> dict:
    return build_inline_keyboard([
        [_btn("Volver", _cb("menu", "main"))],
    ])


def job_panel(running: bool = False) -> dict:
    row = [_btn("Chat", _cb("job", "chat"))]
    if running:
        row.append(_btn("Detener", _cb("job", "stop")))
    row.append(_btn("Estado", _cb("job", "status")))
    return build_inline_keyboard([row])


def active_jobs_list(job_names: list[str]) -> dict:
    rows = []
    for name in job_names[:8]:
        rows.append([_btn(name, _cb("job_detail", name))])
    rows.append([_btn("Volver", _cb("menu", "main"))])
    return build_inline_keyboard(rows)


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


def config_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Modo default", _cb("config", "mode"))],
        [_btn("Scope mode", _cb("config", "scope"))],
        [_btn("Modelo LLM", _cb("config", "llm"))],
        [_btn("Usuarios permitidos", _cb("config", "users"))],
        [_btn("Volver", _cb("menu", "main"))],
    ])


def back_to_menu() -> dict:
    return build_inline_keyboard([
        [_btn("Volver al menú", _cb("menu", "main"))],
    ])


def attachment_offer() -> dict:
    return build_inline_keyboard([
        [
            _btn("Escanear archivo", _cb("menu", "scan_attachment")),
            _btn("Cancelar", _cb("menu", "main")),
        ],
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
