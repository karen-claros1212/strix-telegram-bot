from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message
from strix_telegram_bot.ui.keyboards import (
    job_panel,
    jobs_main_menu,
    back_to_menu,
    parse_callback,
)
from strix_telegram_bot.ui.messages import job_status_text, escape_md
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobPhase
from strix_telegram_bot.security import authorized_only


@authorized_only
def cmd_jobs(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _list_jobs(bot, chat_id)


@authorized_only
def cmd_status(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    store = JobStore()
    active = store.list_active()
    if not active:
        send_message(bot, chat_id, "No hay trabajos activos.", reply_markup=back_to_menu())
        return
    job = active[0]
    text = job_status_text(job)
    send_message(
        bot, chat_id, text,
        reply_markup=job_panel(running=job.is_active),
    )


@authorized_only
def cmd_stop(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    bridge = getattr(bot, "_bridge", None)
    if bridge and bridge.is_running:
        bridge.stop_scan()
        send_message(bot, chat_id, "Escaneo detenido.", reply_markup=back_to_menu())
    else:
        send_message(
            bot, chat_id,
            "No hay escaneo activo para detener.",
            reply_markup=back_to_menu(),
        )


@authorized_only
def callback_jobs(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "list":
        _list_jobs(bot, chat_id, msg_id)

    elif action == "active":
        _list_active_jobs(bot, chat_id, msg_id)

    elif action == "completed":
        _list_jobs_by_status(bot, chat_id, msg_id, JobPhase.COMPLETED)

    elif action == "failed":
        _list_jobs_by_status(bot, chat_id, msg_id, JobPhase.FAILED)

    elif action == "stopped":
        _list_jobs_by_status(bot, chat_id, msg_id, JobPhase.STOPPED)

    elif action == "chat":
        from .chat import callback_chat
        update["callback_query"]["data"] = "chat:enter"
        callback_chat(bot, update)

    elif action == "stop":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            bridge.stop_scan()
            edit_message(
                bot, chat_id, msg_id,
                "Escaneo detenido.",
                reply_markup=back_to_menu(),
            )
        else:
            edit_message(
                bot, chat_id, msg_id,
                "No hay escaneo activo.", reply_markup=back_to_menu(),
            )

    elif action == "status":
        store = JobStore()
        active = store.list_active()
        if active:
            job = active[0]
            text = job_status_text(job)
            edit_message(
                bot, chat_id, msg_id, text,
                reply_markup=job_panel(running=job.is_active),
            )
        else:
            edit_message(
                bot, chat_id, msg_id,
                "No hay trabajos activos.", reply_markup=back_to_menu(),
            )

    elif action == "reports":
        from strix_telegram_bot.commands.reports import _show_reports
        _show_reports(bot, chat_id, msg_id)

    elif action == "caido":
        from strix_telegram_bot.strix.caido_panel import CaidoPanel
        from strix_telegram_bot.jobs.job_store import JobStore
        from strix_telegram_bot.ui.keyboards import caido_main_menu

        store = JobStore()
        active = store.list_active()
        cp = CaidoPanel()
        if active:
            status = cp.build_caido_panel(active[0].run_name)
        else:
            status = cp.build_caido_panel("")
        edit_message(bot, chat_id, msg_id, status, reply_markup=caido_main_menu())

    elif action == "back_menu":
        from strix_telegram_bot.ui.keyboards import main_menu
        from strix_telegram_bot.ui.messages import main_menu_text
        edit_message(
            bot, chat_id, msg_id,
            main_menu_text(), reply_markup=main_menu(),
        )


def _list_jobs(bot, chat_id, msg_id=None) -> None:
    store = JobStore()
    jobs = store.list_recent(limit=10)

    if not jobs:
        text = "No hay trabajos aún."
        kb = back_to_menu()
    else:
        lines = ["Trabajos recientes:"]
        for j in jobs:
            lines.append(
                f"{j.phase.value} {escape_md(j.run_name[:30])} "
                f"[{j.mode.value}] {j.elapsed}"
            )
        text = "\n".join(lines)
        kb = jobs_main_menu()

    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _list_active_jobs(bot, chat_id, msg_id) -> None:
    store = JobStore()
    active = store.list_active()
    if not active:
        edit_message(
            bot, chat_id, msg_id,
            "No hay trabajos activos.", reply_markup=back_to_menu(),
        )
        return
    lines = ["Trabajos activos:"]
    for j in active:
        lines.append(f"  {j.run_name[:30]} [{j.phase.value}] {j.elapsed}")
    edit_message(bot, chat_id, msg_id, "\n".join(lines), reply_markup=back_to_menu())


def _list_jobs_by_status(bot, chat_id, msg_id, status: JobPhase) -> None:
    store = JobStore()
    jobs = store.list_by_status(status, limit=10)
    if not jobs:
        edit_message(
            bot, chat_id, msg_id,
            f"No hay trabajos {status.value}.", reply_markup=back_to_menu(),
        )
        return
    lines = [f"Trabajos {status.value}:"]
    for j in jobs:
        lines.append(f"  {j.run_name[:30]} [{j.mode.value}] {j.elapsed}")
    edit_message(bot, chat_id, msg_id, "\n".join(lines), reply_markup=back_to_menu())


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
