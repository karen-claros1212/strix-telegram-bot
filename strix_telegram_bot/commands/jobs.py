from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message
from strix_telegram_bot.ui.keyboards import (
    job_panel,
    back_to_menu,
    parse_callback,
    main_menu,
)
from strix_telegram_bot.ui.messages import job_status_text, main_menu_text, escape_md
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobPhase


def cmd_jobs(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _list_jobs(bot, chat_id)


def cmd_status(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    bridge = getattr(bot, "_bridge", None)
    if bridge and bridge.is_running:
        status = bridge.to_status_dict()
        text = job_status_text(status)
        send_message(bot, chat_id, text, reply_markup=job_panel(running=True))
        return
    store = JobStore()
    active = store.list_active()
    if not active:
        send_message(bot, chat_id, "No hay trabajos activos.", reply_markup=back_to_menu())
        return
    job = active[0]
    text = job_status_text(job)
    send_message(bot, chat_id, text, reply_markup=job_panel(running=job.is_active))


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

    elif action == "agents":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            agents = bridge.list_agents()
            if agents:
                from strix_telegram_bot.ui.keyboards import agent_selector
                edit_message(bot, chat_id, msg_id, "Selecciona un agente:", reply_markup=agent_selector(agents))
            else:
                edit_message(bot, chat_id, msg_id, "No hay agentes disponibles.", reply_markup=back_to_menu())
        else:
            edit_message(bot, chat_id, msg_id, "Bridge no disponible.", reply_markup=back_to_menu())

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
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            status = bridge.to_status_dict()
            text = job_status_text(status)
            edit_message(bot, chat_id, msg_id, text, reply_markup=job_panel(running=True))
        else:
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
        kb = back_to_menu()

    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=kb)
    else:
        send_message(bot, chat_id, text, reply_markup=kb)


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
