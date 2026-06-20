from __future__ import annotations

from typing import Any, Optional

from strix_telegram_bot.models import JobPhase, ScanMode, BridgePhase

_PHASE_LABELS = {
    "initializing": "inicializando",
    "running": "ejecutando",
    "waiting": "esperando",
    "completed": "completado",
    "failed": "falló",
    "stopped": "detenido",
}

_MODE_LABELS = {
    ScanMode.QUICK: "Rápido",
    ScanMode.STANDARD: "Estándar",
    ScanMode.DEEP: "Profundo",
}


def escape_md(text: str) -> str:
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, "\\" + ch)
    return text


def main_menu_text() -> str:
    return (
        "Centro de Control STRIX\n"
        "\n"
        "Presioná Escanear y enviá los targets.\n"
        "Podés mandar URLs, rutas locales, o repos GitHub.\n"
        "Separalos con coma o en líneas nuevas."
    )


def waiting_for_targets_text() -> str:
    return "¿Qué querés escanear? Enviá URLs, repos, o rutas locales."


def job_status_text(status: dict, last_tool: str = "", last_tool_status: str = "") -> str:
    phase_str = status.get("phase", "unknown")
    phase_label = _PHASE_LABELS.get(phase_str, phase_str)
    mode_str = status.get("mode", "deep")

    try:
        mode = ScanMode(mode_str)
        mode_label = _MODE_LABELS.get(mode, mode_str)
    except ValueError:
        mode_label = mode_str.upper()

    target = status.get("target", [])
    elapsed = status.get("elapsed", "0s")
    error = status.get("error")
    awaiting = status.get("awaiting_input", False)
    prompt = status.get("input_prompt")

    lines = [
        f"STRIX {mode_label}",
        f"Fase: {phase_label}",
        f"Tiempo: {elapsed}",
    ]

    if target:
        target_str = ", ".join(target) if isinstance(target, list) else str(target)
        lines.append(f"Objetivo: {escape_md(target_str)}")

    if last_tool:
        icon = {"ejecutando": "▶", "completado": "✅", "cancelada": "⏹"}.get(last_tool_status, "")
        lines.append(f"{icon} Herramienta: {escape_md(last_tool[:40])}")

    if error:
        lines.append("")
        lines.append(f"⚠ Error: {escape_md(error)}")

    if awaiting and prompt:
        lines.append("")
        lines.append(f"⏳ STRIX necesita información: {escape_md(prompt)}")

    return "\n".join(lines)


def _status_icon(st: str) -> str:
    return {
        "running": "▶",
        "waiting": "⏳",
        "completed": "✅",
        "failed": "❌",
        "stopped": "⏹",
    }.get(st, "?")


def help_text() -> str:
    return (
        "Centro de Control STRIX — Ayuda\n"
        "\n"
        "/status — Estado actual\n"
        "/stop — Detener escaneo\n"
        "/jobs — Historial de trabajos\n"
        "/reports — Centro de reportes\n"
        "/health — Estado del sistema\n"
        "\n"
        "Durante un escaneo, cualquier mensaje de texto\n"
        "se envía automáticamente al agente STRIX.\n"
        "Usá el botón Escanear para iniciar un escaneo."
    )


# Legacy helpers (still used by other modules)

def health_text(
    strix_version: str,
    python_version: str,
    uptime: str,
    active_jobs: int,
    caido_status: str,
) -> str:
    return (
        "Estado de STRIX\n"
        f"Versión: {escape_md(strix_version)}\n"
        f"Python: {escape_md(python_version)}\n"
        f"Activo: {uptime}\n"
        f"Trabajos activos: {active_jobs}\n"
        f"Caido: {escape_md(caido_status)}"
    )


def reports_menu_text() -> str:
    return (
        "Centro de Reportes\n"
        "\n"
        "Navegá, descargá y revisá resultados de STRIX."
    )


def evidence_text(manifest: dict) -> str:
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        return "No hay evidencia guardada."
    total_size = sum(a.get("size_bytes", 0) for a in artifacts)
    sensitive = sum(1 for a in artifacts if a.get("sensitive"))
    return (
        "Bóveda de Evidencia\n"
        f"Archivos: {len(artifacts)}\n"
        f"Tamaño: {total_size / 1024:.1f} KB\n"
        f"Items sensibles: {sensitive}"
    )


def config_text(settings_dict: dict) -> str:
    lines = ["Configuración:"]
    for k, v in settings_dict.items():
        lines.append(f"  {escape_md(k)}: {escape_md(str(v))}")
    return "\n".join(lines)
