from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import JobState, JobPhase, ScanMode

_PHASE_ICONS = {
    JobPhase.CREATED: "created",
    JobPhase.CONFIGURING: "configuring",
    JobPhase.SCANNING: "scanning",
    JobPhase.BROWSER: "browser",
    JobPhase.PROXY: "proxy",
    JobPhase.ANALYZING: "analyzing",
    JobPhase.REPORTING: "reporting",
    JobPhase.COMPLETED: "completed",
    JobPhase.FAILED: "failed",
    JobPhase.STOPPED: "stopped",
}

_MODE_ICONS = {
    ScanMode.QUICK: "Quick",
    ScanMode.STANDARD: "Standard",
    ScanMode.DEEP: "Deep",
}


def escape_md(text: str) -> str:
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, "\\" + ch)
    return text


def main_menu_text(strix_version: str = "") -> str:
    ver = f" STRIX {strix_version}" if strix_version else ""
    return (
        f"STRIX Control Center{ver}\n"
        "\n"
        "Remote UI for STRIX v1"
        "\n"
        "Select an option below:"
    )


def job_status_text(job: JobState) -> str:
    phase_icon = _PHASE_ICONS.get(job.phase, "?")
    mode_icon = _MODE_ICONS.get(job.mode, job.mode.value)

    lines = [
        f"STRIX {mode_icon} Scan",
        f"Target: {escape_md(', '.join(job.target))}",
        f"Status: {phase_icon}",
        f"Phase: {job.phase.value}",
        f"Time: {job.elapsed}",
    ]

    if job.caido_url:
        lines.append(f"Caido: {escape_md(job.caido_url)}")

    if job.error:
        lines.append(f"Error: {escape_md(job.error)}")

    if job.awaiting_input and job.input_prompt:
        lines.append(f"\nSTRIX needs input: {escape_md(job.input_prompt)}")

    return "\n".join(lines)


def job_completed_text(job: JobState) -> str:
    phase_icon = _PHASE_ICONS.get(job.phase, "?")
    return (
        f"STRIX {_MODE_ICONS.get(job.mode, job.mode.value)} Scan\n"
        f"Target: {escape_md(', '.join(job.target))}\n"
        f"Status: {phase_icon}\n"
        f"Duration: {job.elapsed}\n"
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
        f"STRIX Health\n"
        f"Version: {escape_md(strix_version)}\n"
        f"Python: {escape_md(python_version)}\n"
        f"Uptime: {uptime}\n"
        f"Active jobs: {active_jobs}\n"
        f"Caido: {escape_md(caido_status)}"
    )


def reports_menu_text() -> str:
    return (
        "Report Center\n"
        "\n"
        "Browse, download, and review STRIX scan results."
    )


def evidence_text(manifest: dict) -> str:
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        return "No evidence stored."

    total_size = sum(a.get("size_bytes", 0) for a in artifacts)
    sensitive = sum(1 for a in artifacts if a.get("sensitive"))

    return (
        f"Evidence Vault\n"
        f"Artifacts: {len(artifacts)}\n"
        f"Size: {total_size / 1024:.1f} KB\n"
        f"Sensitive items: {sensitive}"
    )


def caido_panel_text(url: Optional[str], active: bool) -> str:
    if url:
        return f"Caido Proxy\nURL: {escape_md(url)}\nStatus: Active"
    if active:
        return "Caido Proxy\nStatus: Active (URL unknown)"
    return "Caido Proxy\nStatus: Inactive\n\nStart a scan to see Caido here."


def instruction_text() -> str:
    return (
        "Instruction / Focus\n\n"
        "Choose a focus area to guide STRIX, "
        "or skip for a general-purpose scan."
    )


def tools_panel_text(active_tools: list[dict]) -> str:
    if not active_tools:
        return "No tools detected in the current scan."
    lines = ["Active Tools:"]
    for t in active_tools:
        name = t.get("name", "unknown")
        status = t.get("status", "running")
        lines.append(f"  {name} — {status}")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "STRIX Control Center Help\n"
        "\n"
        "New Pentest - Start a penetration test\n"
        "  Quick: Fast CI/CD smoke test\n"
        "  Standard: Routine security review\n"
        "  Deep: Full pentest (default)\n"
        "\n"
        "Profiles:\n"
        "  Interactive / TUI — default, mirrors STRIX interactive UI\n"
        "  Headless / CI — non-interactive, automated\n"
        "\n"
        "Focus Presets:\n"
        "  Business Logic, Auth/JWT, SQLi, XSS,\n"
        "  SSRF/XXE, K8s/Infra, Secrets, Custom\n"
        "\n"
        "Chat - Interact with STRIX or respond to prompts\n"
        "Jobs - View active scans\n"
        "Reports - View completed scan results\n"
        "Evidence - Browse scan artifacts\n"
        "Tools - View active STRIX tools\n"
        "Caido - Open the proxy inspector\n"
        "Health - System status\n"
        "Config - Change settings"
    )


def config_text(settings_dict: dict) -> str:
    lines = ["Configuration:"]
    for k, v in settings_dict.items():
        lines.append(f"  {escape_md(k)}: {escape_md(str(v))}")
    return "\n".join(lines)
