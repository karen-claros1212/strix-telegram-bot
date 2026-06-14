from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import main_menu, parse_callback, target_type_selector
from strix_telegram_bot.ui.messages import main_menu_text, help_text
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import MenuState
from strix_telegram_bot.security import authorized_only


@authorized_only
def cmd_start(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    chat_mode = getattr(bot, "_chat_mode", {})
    chat_mode.pop(chat_id, None)
    text = main_menu_text()
    send_message(bot, chat_id, text, reply_markup=main_menu())


@authorized_only
def cmd_help(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    send_message(bot, chat_id, help_text(), reply_markup=main_menu())


@authorized_only
def callback_menu(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    pm = get_panel_manager(chat_id)

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "main":
        pm.back_to_main()
        text = main_menu_text()
        edit_message(
            bot, chat_id, msg_id, text,
            reply_markup=main_menu(),
        )

    elif action == "new_pentest":
        pm.push(MenuState.NEW_PENTEST_TARGET)
        edit_message(
            bot, chat_id, msg_id,
            "Seleccioná tipo de objetivo:",
            reply_markup=target_type_selector(),
        )

    elif action == "scan_attachment":
        pending = getattr(bot, "_pending_attachment", None)
        if pending:
            pm._selected_targets = [pending]
            pm.push(MenuState.NEW_PENTEST_PROFILE)
            from strix_telegram_bot.ui.keyboards import profile_selector
            edit_message(
                bot, chat_id, msg_id,
                f"Objetivo: {pending}\nSeleccioná perfil:",
                reply_markup=profile_selector(),
            )
        else:
            edit_message(
                bot, chat_id, msg_id,
                "No hay archivo pendiente.", reply_markup=main_menu(),
            )

    elif action == "chat":
        bot._chat_mode[chat_id] = True
        edit_message(
            bot, chat_id, msg_id,
            "Modo chat activado. Enviá un mensaje para interactuar con STRIX.\n\n"
            "Si hay un trabajo en ejecución esperando input, "
            "tu mensaje se enviará como respuesta.\n\n"
            "Escribí /start para salir del modo chat.",
            reply_markup=main_menu(),
        )

    elif action == "jobs":
        _show_jobs(bot, chat_id, msg_id)

    elif action == "reports":
        from strix_telegram_bot.commands.reports import _show_reports
        _show_reports(bot, chat_id, msg_id)

    elif action == "caido":
        _show_caido_panel(bot, chat_id, msg_id)

    elif action == "evidence":
        _show_evidence(bot, chat_id, msg_id)

    elif action == "tools":
        _show_tools(bot, chat_id, msg_id)

    elif action == "evidence_list":
        from strix_telegram_bot.ui.keyboards import evidence_list_menu
        from strix_telegram_bot.strix.evidence_vault import EvidenceVault
        from strix_telegram_bot.jobs.job_store import JobStore
        from strix_telegram_bot.ui.messages import evidence_text
        store = JobStore()
        jobs = [j for j in store.list_recent(5) if j.is_terminal and j.run_name != "pending"]
        if jobs:
            vault = EvidenceVault(jobs[0].run_name)
            artifacts = vault.list_evidence()
            if artifacts:
                text = evidence_text(vault.get_manifest())
                edit_message(bot, chat_id, msg_id, text, reply_markup=evidence_list_menu(artifacts))
            else:
                edit_message(bot, chat_id, msg_id, "No hay evidencia.", reply_markup=main_menu())
        else:
            edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=main_menu())

    elif action == "health":
        _show_health(bot, chat_id, msg_id)

    elif action == "config":
        pm.push(MenuState.CONFIG)
        from strix_telegram_bot.ui.keyboards import config_menu
        edit_message(
            bot, chat_id, msg_id,
            "Configuración:", reply_markup=config_menu(),
        )

    elif action == "help":
        edit_message(
            bot, chat_id, msg_id, help_text(),
            reply_markup=main_menu(),
        )



def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )


def _show_jobs(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.ui.keyboards import jobs_main_menu, back_to_menu
    from strix_telegram_bot.jobs.job_store import JobStore
    from strix_telegram_bot.ui.messages import escape_md

    store = JobStore()
    active = store.list_active()
    all_jobs = store.list_recent(limit=5)

    lines = ["Resumen de trabajos:"]
    if active:
        lines.append(f"Activos: {len(active)}")
    lines.append(f"Recientes: {len(all_jobs)}")

    if active:
        for j in active[:3]:
            lines.append(
                f"  {j.run_name[:30]} [{j.phase.value}] {j.elapsed}"
            )

    edit_message(
        bot, chat_id, msg_id, "\n".join(lines),
        reply_markup=jobs_main_menu(),
    )


def _show_caido_panel(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.ui.keyboards import caido_main_menu
    from strix_telegram_bot.strix.caido_panel import CaidoPanel
    from strix_telegram_bot.jobs.job_store import JobStore

    store = JobStore()
    active = store.list_active()
    cp = CaidoPanel()

    if active:
        job = active[0]
        status = cp.build_caido_panel(job.run_name)
    else:
        status = cp.build_caido_panel("")

    edit_message(bot, chat_id, msg_id, status, reply_markup=caido_main_menu())


def _show_health(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.ui.messages import health_text
    from strix_telegram_bot.ui.keyboards import back_to_menu

    import subprocess
    import platform

    try:
        ver = subprocess.run(
            ["strix", "--version"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        ver = "unknown"

    from strix_telegram_bot.jobs.job_store import JobStore
    store = JobStore()
    active_count = store.count_active()

    text = health_text(
        strix_version=ver,
        python_version=platform.python_version(),
        uptime="N/A",
        active_jobs=active_count,
        caido_status="N/A",
    )
    edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())


def _show_evidence(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.strix.evidence_vault import EvidenceVault
    from strix_telegram_bot.ui.keyboards import evidence_list_menu
    from strix_telegram_bot.ui.messages import evidence_text
    from strix_telegram_bot.jobs.job_store import JobStore

    store = JobStore()
    jobs = [j for j in store.list_recent(5) if j.is_terminal and j.run_name != "pending"]
    if not jobs:
        edit_message(bot, chat_id, msg_id, "No hay trabajos completados.", reply_markup=main_menu())
        return

    vault = EvidenceVault(jobs[0].run_name)
    artifacts = vault.list_evidence()
    if not artifacts:
        edit_message(bot, chat_id, msg_id, "No hay evidencia disponible.", reply_markup=main_menu())
        return

    text = evidence_text(vault.get_manifest())
    edit_message(bot, chat_id, msg_id, text, reply_markup=evidence_list_menu(artifacts))


def _show_tools(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.strix.report_collector import ReportCollector
    from strix_telegram_bot.ui.keyboards import tools_panel
    from strix_telegram_bot.ui.messages import tools_panel_text
    from strix_telegram_bot.jobs.job_store import JobStore

    store = JobStore()
    active = store.list_active()
    active_tools: list[dict] = []

    if active:
        job = active[0]
        rc = ReportCollector(job.run_name)
        events = rc.get_json_events()
        if events:
            seen: set[str] = set()
            for ev in events:
                tool = ev.get("data", {}).get("tool", "")
                if tool and tool not in seen:
                    seen.add(tool)
                    active_tools.append({"name": tool, "status": "active"})

    text = tools_panel_text(active_tools)
    edit_message(bot, chat_id, msg_id, text, reply_markup=tools_panel(active_tools))
