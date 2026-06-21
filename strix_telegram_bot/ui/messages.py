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


def job_status_text(status: dict, tool_state: dict | None = None) -> str:
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
        f"STRIX {mode_label} — {phase_label.capitalize()}",
    ]

    if target:
        target_str = ", ".join(target) if isinstance(target, list) else str(target)
        lines.append(f"Objetivo: {target_str}")

    if tool_state:
        streaming = tool_state.get("streaming", False)
        current_name = tool_state.get("current_tool_name", "")
        current_args = tool_state.get("current_tool_args") or {}
        active = tool_state.get("active_count", 0)
        completed = tool_state.get("completed_count", 0)
        failed = tool_state.get("failed_count", 0)
        agent_name = tool_state.get("active_agent_name", "")
        panel_awaiting = tool_state.get("awaiting_input", False)
        panel_prompt = tool_state.get("input_prompt", "")

        # Activity priority: tool > streaming > awaiting > idle
        if current_name:
            tool_label, action_label = describe_tool_activity(current_name, current_args)
            lines.append(f"▶ {tool_label}")
            lines.append(f"   {action_label}")
        elif streaming:
            lines.append("Actividad: Redactando una respuesta")
        elif panel_awaiting:
            if panel_prompt:
                lines.append(f"Estado: {panel_prompt}")
            else:
                lines.append("Estado: Disponible para recibir instrucciones")
        else:
            lines.append("Actividad: Analizando resultados")

        if agent_name and agent_name != "STRIX":
            lines.append(f"Agente: {agent_name}")

        tool_summary_parts = []
        if completed:
            tool_summary_parts.append(f"{completed} completadas")
        if active:
            tool_summary_parts.append(f"{active} activas")
        if failed:
            tool_summary_parts.append(f"{failed} fallidas")
        if tool_summary_parts:
            lines.append(f"{' · '.join(tool_summary_parts)}")

    lines.append(f"Tiempo: {elapsed}")

    if error:
        lines.append("")
        lines.append(f"⚠ Error: {error}")

    return "\n".join(lines)


def _status_icon(st: str) -> str:
    return {
        "running": "▶",
        "waiting": "⏳",
        "completed": "✅",
        "failed": "❌",
        "stopped": "⏹",
    }.get(st, "?")


def describe_tool_activity(tool_name: str, args: dict | None = None) -> tuple[str, str]:
    """Translate internal tool names into human-readable tool + action labels."""
    args = args or {}
    name_lower = tool_name.lower() if tool_name else ""

    if "scope_rules/list" in name_lower or name_lower == "list_scope_rules":
        return ("Alcance", "Consultando reglas del objetivo")
    if "scope_rules/create" in name_lower or name_lower == "create_scope_rule":
        return ("Alcance", "Configurando dominios permitidos")
    if "list_sitemap" in name_lower:
        return ("Mapa del sitio", "Consultando rutas y endpoints")
    if "list_requests" in name_lower:
        return ("Tráfico HTTP", "Revisando solicitudes capturadas")
    if "agent-browser" in name_lower or "agent_browser" in name_lower:
        action = "Navegando"
        if args:
            url = args.get("url", "") or args.get("target", "")
            if url:
                action = f"Abriendo {str(url)[:60]}"
        return ("Navegador", action)
    if "snapshot" in name_lower and ("browser" in name_lower or "page" in name_lower):
        return ("Navegador", "Analizando la estructura de la página")
    if "view_image" in name_lower or "screenshot" in name_lower:
        return ("Visión", "Analizando una captura de pantalla")
    if "nuclei" in name_lower:
        return ("Nuclei", "Buscando vulnerabilidades conocidas")
    if "subfinder" in name_lower:
        return ("Subfinder", "Buscando subdominios")
    if "ffuf" in name_lower:
        return ("FFUF", "Descubriendo rutas y archivos")
    if "curl" in name_lower or "http_request" in name_lower:
        url = args.get("url", "") or args.get("target", "")
        action = f"Consultando {str(url)[:50]}" if url else "Revisando respuesta HTTP"
        return ("HTTP", action)
    if "create_vulnerability_report" in name_lower or "report_vulnerability" in name_lower:
        return ("Reporte", "Registrando vulnerabilidad")
    if "exec_command" in name_lower or "execute_command" in name_lower or "run_command" in name_lower:
        cmd = args.get("cmd", "") or args.get("command", "") or ""
        exe = str(cmd).split()[0] if cmd else "comando"
        return ("Ejecutar", f"{exe[:40]}")
    if "start_proxy" in name_lower or "ensure_proxy" in name_lower:
        return ("Proxy", "Iniciando proxy de captura")
    if "stop_proxy" in name_lower:
        return ("Proxy", "Deteniendo proxy")
    if "write_file" in name_lower or "save_file" in name_lower:
        return ("Archivos", "Guardando resultado")
    if "read_file" in name_lower:
        fname = str(args.get("path", "") or args.get("file", "")).split("/")[-1][:30]
        return ("Archivos", f"Leyendo {fname}" if fname else "Leyendo archivo")

    # Fallback: use the tool name directly, cleaned
    clean = tool_name.replace("_", " ").replace("-", " ")
    return (clean.capitalize()[:40], "Ejecutando")


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
