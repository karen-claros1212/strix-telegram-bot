"""Textos en español para la UI del bot."""

from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import JobState, JobPhase, ScanMode

_PHASE_ICONS = {
    JobPhase.CREATED: "creado",
    JobPhase.CONFIGURING: "configurando",
    JobPhase.SCANNING: "escaneando",
    JobPhase.BROWSER: "navegador",
    JobPhase.PROXY: "proxy",
    JobPhase.ANALYZING: "analizando",
    JobPhase.REPORTING: "reportando",
    JobPhase.COMPLETED: "completado",
    JobPhase.FAILED: "falló",
    JobPhase.STOPPED: "detenido",
}

_MODE_ICONS = {
    ScanMode.QUICK: "Rápido",
    ScanMode.STANDARD: "Estándar",
    ScanMode.DEEP: "Profundo",
}


def escape_md(text: str) -> str:
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, "\\" + ch)
    return text


def main_menu_text(strix_version: str = "") -> str:
    ver = f" STRIX {strix_version}" if strix_version else ""
    return (
        f"Centro de Control STRIX{ver}\n"
        "\n"
        "Elegí una opción:"
    )


def job_status_text(job: JobState | dict) -> str:
    if isinstance(job, dict):
        phase_str = job.get("phase", "unknown")
        try:
            phase = JobPhase(phase_str)
        except ValueError:
            phase = None
        mode_str = job.get("mode", "deep")
        try:
            mode = ScanMode(mode_str)
        except ValueError:
            mode = None

        phase_icon = _PHASE_ICONS.get(phase, phase_str) if phase else phase_str
        mode_icon = _MODE_ICONS.get(mode, mode_str) if mode else mode_str.upper()
        target = job.get("target", [])
        elapsed = job.get("elapsed", "0s")
        error = job.get("error")
        awaiting = job.get("awaiting_input", False)
        prompt = job.get("input_prompt")
    else:
        phase_icon = _PHASE_ICONS.get(job.phase, "?")
        mode_icon = _MODE_ICONS.get(job.mode, job.mode.value)
        target = job.target
        elapsed = job.elapsed
        error = job.error
        awaiting = job.awaiting_input
        prompt = job.input_prompt
        phase = job.phase

    lines = [
        f"Escaneo {mode_icon}",
        f"Objetivo: {escape_md(', '.join(target) if isinstance(target, list) else str(target))}",
        f"Estado: {phase_icon}",
        f"Fase: {phase.value if isinstance(phase, JobPhase) else phase}",
        f"Tiempo: {elapsed}",
    ]

    if isinstance(job, JobState) and job.caido_url:
        lines.append(f"Caido: {escape_md(job.caido_url)}")

    if error:
        lines.append(f"Error: {escape_md(error)}")

    if awaiting and prompt:
        lines.append(f"\nSTRIX necesita información: {escape_md(prompt)}")

    return "\n".join(lines)


def job_completed_text(job: JobState) -> str:
    phase_icon = _PHASE_ICONS.get(job.phase, "?")
    return (
        f"Escaneo {_MODE_ICONS.get(job.mode, job.mode.value)}\n"
        f"Objetivo: {escape_md(', '.join(job.target))}\n"
        f"Estado: {phase_icon}\n"
        f"Duración: {job.elapsed}\n"
        f"{'Error: '+escape_md(job.error) if job.error else ''}"
    )


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


def caido_panel_text(url: Optional[str], active: bool) -> str:
    if url:
        return f"Proxy Caido\nURL: {escape_md(url)}\nEstado: Activo"
    if active:
        return "Proxy Caido\nEstado: Activo (URL desconocida)"
    return "Proxy Caido\nEstado: Inactivo\n\nIniciá un escaneo para ver Caido."


def instruction_text() -> str:
    return (
        "Instrucción / Enfoque\n\n"
        "Elegí un área de enfoque para guiar a STRIX, "
        "o saltá para un escaneo de propósito general."
    )


def tools_panel_text(active_tools: list[dict]) -> str:
    if not active_tools:
        return "No se detectaron herramientas en el escaneo actual."
    lines = ["Herramientas activas:"]
    for t in active_tools:
        name = t.get("name", "desconocida")
        status = t.get("status", "ejecutando")
        lines.append(f"  {name} — {status}")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "Centro de Control STRIX — Ayuda\n"
        "\n"
        "Escanear — Iniciar una prueba de penetración\n"
        "  Rápido: Test rápido CI/CD\n"
        "  Estándar: Revisión de seguridad\n"
        "  Profundo: Auditoría completa (default)\n"
        "\n"
        "Perfiles:\n"
        "  Interactivo — espejo de la TUI de STRIX\n"
        "  Headless / CI — automático, sin interfaz\n"
        "\n"
        "Chat — Hablá con STRIX o respondé a sus preguntas\n"
        "  /chat — entrar/salir del modo Chat\n"
        "  Mientras estás en Chat, cualquier texto va al agente\n"
        "Detener — Detiene el escaneo activo\n"
        "Estado — Muestra el estado del escaneo actual"
    )


def config_text(settings_dict: dict) -> str:
    lines = ["Configuración:"]
    for k, v in settings_dict.items():
        lines.append(f"  {escape_md(k)}: {escape_md(str(v))}")
    return "\n".join(lines)
