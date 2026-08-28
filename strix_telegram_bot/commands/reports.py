from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import reports_list, back_to_menu, parse_callback, report_detail_menu
from strix_telegram_bot.ui.messages import escape_md
from strix_telegram_bot.strix.report_collector import ReportCollector
from strix_telegram_bot.strix.report_delivery import deliver_report_document
from strix_telegram_bot.strix.evidence_vault import EvidenceVault
from strix_telegram_bot.strix.runtime_bridge import _run_dir_for
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobPhase

_MAX_MSG = 4000


def _is_run_json_completed(run_name: str) -> bool:
    """Check that run.json exists and has status == 'completed'.

    Uses the official run_dir_for() as the single path authority for run
    artifacts (not settings.strix_runs_dir / run_name).
    """
    try:
        if _run_dir_for is None:
            return False
        run_dir = _run_dir_for(run_name)
        run_json = run_dir / "run.json"
        if run_json.exists():
            data = _json.loads(run_json.read_text())
            return data.get("status") == "completed"
    except Exception:
        pass
    return False


def _split_smart(text: str, max_len: int = _MAX_MSG) -> list[str]:
    """Split text into chunks that prefer breaking at newlines."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _send_fragmented(bot: Any, chat_id: int, text: str) -> bool:
    """Send text as one or more Telegram-safe fragments.

    Returns True if all fragments were delivered successfully.
    """
    for chunk in _split_smart(text):
        resp = send_message(bot, chat_id, chunk, parse_mode=None)
        if not resp or not resp.get("message_id"):
            return False
    return True
def cmd_reports(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _show_reports(bot, chat_id)


def callback_reports(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    action = parts[1]
    store = JobStore()

    if action == "list":
        _show_reports(bot, chat_id, msg_id)

    elif action == "latest":
        _send_latest_report(bot, chat_id, msg_id)

    elif action == "summary":
        _send_executive_summary(bot, chat_id, msg_id)

    elif action == "history":
        _show_report_history(bot, chat_id, msg_id)

    elif action == "markdown":
        _send_report_type(bot, chat_id, msg_id, "markdown")

    elif action == "detail":
        run_name = parts[2] if len(parts) > 2 else ""
        _show_run_detail(bot, chat_id, msg_id, run_name)

    elif action == "download_md":
        run_name = parts[2] if len(parts) > 2 else ""
        _deliver_report_document(bot, chat_id, msg_id, run_name)

    elif action == "csv":
        _send_report_type(bot, chat_id, msg_id, "csv")

    elif action == "json":
        _send_report_type(bot, chat_id, msg_id, "json")

    elif action == "evidence":
        _show_evidence_for_latest(bot, chat_id, msg_id)

    elif action == "cleanup":
        count = store.cleanup_old(days=30)
        edit_message(
            bot, chat_id, msg_id,
            f"Se limpiaron {count} trabajos viejos.",
            reply_markup=back_to_menu(),
        )

    elif len(parts) >= 3 and parts[0] == "report":
        report_name = parts[1]
        job_name = parts[2] if len(parts) > 2 else "unknown"
        rc = ReportCollector(job_name)
        content = rc.get_report_content(report_name, max_chars=None)
        if content:
            _send_fragmented(bot, chat_id, f"Reporte: {report_name}\n\n{content}")
        else:
            send_message(bot, chat_id, f"Reporte {report_name} no encontrado.")


def _show_reports(bot, chat_id, msg_id=None) -> None:
    runs = [j["run_name"] for j in ReportCollector.list_jobs_with_reports(limit=8)]

    if not runs:
        text = "No hay reportes disponibles. Completá un escaneo primero."
        kb = back_to_menu()
    else:
        text = "Seleccioná un escaneo para ver sus reportes:"
        kb = reports_list(runs)

    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _show_run_detail(bot, chat_id, msg_id, run_name: str = "") -> None:
    if not run_name:
        edit_message(bot, chat_id, msg_id, "Escaneo no encontrado.", reply_markup=back_to_menu())
        return

    text = (
        f"Reporte de `{escape_md(run_name)}`\n"
        "\n"
        "Elegí una opción para este escaneo."
    )
    kb = report_detail_menu(run_name)
    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _send_latest_report(bot, chat_id, msg_id) -> None:
    store = JobStore()
    jobs = [j for j in store.list_recent(10)
            if j.phase == JobPhase.COMPLETED
            and j.run_name != "pending"
            and _is_run_json_completed(j.run_name)]
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=back_to_menu())
        return

    for job in jobs:
        rc = ReportCollector(job.run_name)
        content = rc.get_full_markdown_report()
        if not content or not content.strip():
            continue
        edit_message(bot, chat_id, msg_id, f"Enviando reporte de {job.run_name}…", reply_markup=back_to_menu())
        ok = _send_fragmented(bot, chat_id, f"Reporte de {job.run_name}:\n\n{content}")
        status = "Reporte enviado." if ok else "Error al enviar reporte. Intentá desde Reportes."
        edit_message(bot, chat_id, msg_id, status, reply_markup=back_to_menu())
        return

    edit_message(bot, chat_id, msg_id, "No se encontraron reportes válidos.", reply_markup=back_to_menu())


def _send_executive_summary(bot, chat_id, msg_id) -> None:
    store = JobStore()
    jobs = [j for j in store.list_recent(10)
            if j.phase == JobPhase.COMPLETED
            and j.run_name != "pending"
            and _is_run_json_completed(j.run_name)]
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=back_to_menu())
        return

    for job in jobs:
        rc = ReportCollector(job.run_name)
        summary = rc.build_executive_summary()
        if summary:
            edit_message(bot, chat_id, msg_id, summary, reply_markup=back_to_menu())
            return

    edit_message(bot, chat_id, msg_id, "No hay resumen disponible.", reply_markup=back_to_menu())


def _show_report_history(bot, chat_id, msg_id) -> None:
    jobs = ReportCollector.list_jobs_with_reports(limit=8)
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay historial de reportes.", reply_markup=back_to_menu())
        return
    lines = ["Historial de reportes:"]
    for j in jobs:
        lines.append(f"  {j['run_name']} ({j['report_count']} reportes)")
    edit_message(bot, chat_id, msg_id, "\n".join(lines), reply_markup=back_to_menu())


def _send_report_type(bot, chat_id, msg_id, rtype: str) -> None:
    store = JobStore()
    jobs = [j for j in store.list_recent(10)
            if j.phase == JobPhase.COMPLETED
            and j.run_name != "pending"
            and _is_run_json_completed(j.run_name)]
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=back_to_menu())
        return

    label = rtype.upper()
    for job in jobs:
        rc = ReportCollector(job.run_name)
        content = None
        if rtype == "markdown":
            content = rc.get_full_markdown_report()
        elif rtype == "csv":
            content = rc.get_report_content("vulnerabilities.csv", max_chars=None)
        elif rtype == "json":
            events = rc.get_json_events()
            if events:
                content = _json.dumps(events[:50], indent=2)

        if content and content.strip():
            edit_message(bot, chat_id, msg_id, f"Enviando reporte {label}…", reply_markup=back_to_menu())
            ok = _send_fragmented(bot, chat_id, f"Reporte {label}:\n\n{content}")
            status = "Reporte enviado." if ok else "Error al enviar reporte. Intentá desde Reportes."
            edit_message(bot, chat_id, msg_id, status, reply_markup=back_to_menu())
            return

    edit_message(bot, chat_id, msg_id, f"No hay reporte {label} disponible.", reply_markup=back_to_menu())


def _deliver_report_document(bot, chat_id, msg_id, run_name: str = "") -> None:
    """Deliver the selected run's report as a single Markdown document.

    No fallback: the run_name must come from the report detail callback.
    """
    if not run_name:
        edit_message(bot, chat_id, msg_id, "Informe no disponible: falta el escaneo seleccionado.", reply_markup=back_to_menu())
        return

    if not _is_run_json_completed(run_name):
        edit_message(
            bot, chat_id, msg_id,
            "El análisis terminó con error antes de generar el informe oficial.\n"
            "No se produjo ningún archivo Markdown para este run.",
            reply_markup=back_to_menu(),
        )
        return

    edit_message(bot, chat_id, msg_id, "Descargando informe completo…", reply_markup=back_to_menu())
    result = deliver_report_document(bot, chat_id, run_name)
    if result == "delivered":
        edit_message(bot, chat_id, msg_id, "Informe completo enviado como archivo Markdown.", reply_markup=back_to_menu())
    elif result == "send_failed":
        edit_message(bot, chat_id, msg_id, "Error al enviar el informe.", reply_markup=back_to_menu())
    else:
        edit_message(bot, chat_id, msg_id, "Informe no disponible.", reply_markup=back_to_menu())


def _show_evidence_for_latest(bot, chat_id, msg_id) -> None:
    store = JobStore()
    jobs = [j for j in store.list_recent(10)
            if j.phase == JobPhase.COMPLETED
            and j.run_name != "pending"
            and _is_run_json_completed(j.run_name)]
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=back_to_menu())
        return

    for job in jobs:
        vault = EvidenceVault(job.run_name)
        ev_summary = vault.summary()
        if ev_summary and ev_summary.strip() and "No hay" not in ev_summary:
            edit_message(bot, chat_id, msg_id, ev_summary, reply_markup=back_to_menu())
            return

    edit_message(bot, chat_id, msg_id, "No se encontró evidencia válida.", reply_markup=back_to_menu())


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
