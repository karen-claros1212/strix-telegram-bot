from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import (
    job_panel,
    active_jobs_list,
    back_to_menu,
    parse_callback,
)
from strix_telegram_bot.ui.messages import job_status_text, job_completed_text, escape_md
from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.jobs.process_control import ProcessController
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
        send_message(bot, chat_id, "No active jobs.", reply_markup=back_to_menu())
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
    store = JobStore()
    ctrl = ProcessController()
    active = store.list_active()
    if not active:
        send_message(bot, chat_id, "No active jobs to stop.", reply_markup=back_to_menu())
        return
    job = active[0]
    stopped = ctrl.stop(job.run_name)
    if stopped:
        job.phase = "stopped"
        store.save(job)
        send_message(
            bot, chat_id,
            f"Job {job.run_name} stopped.",
            reply_markup=back_to_menu(),
        )
    else:
        send_message(
            bot, chat_id,
            f"Failed to stop {job.run_name}. Process may already be dead.",
            reply_markup=back_to_menu(),
        )


@authorized_only
def callback_jobs(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    answer_callback(bot, cb.get("id", ""))

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "list":
        _list_jobs(bot, chat_id, msg_id)

    elif action == "chat":
        edit_message(
            bot, chat_id, msg_id,
            "Send a message to respond to STRIX.",
            reply_markup=back_to_menu(),
        )

    elif action == "stop":
        store = JobStore()
        ctrl = ProcessController()
        active = store.list_active()
        if active:
            job = active[0]
            ctrl.stop(job.run_name)
            job.phase = "stopped"
            store.save(job)
            edit_message(
                bot, chat_id, msg_id,
                f"Job {job.run_name} stopped.",
                reply_markup=back_to_menu(),
            )
        else:
            edit_message(
                bot, chat_id, msg_id,
                "No active jobs.", reply_markup=back_to_menu(),
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
                "No active jobs.", reply_markup=back_to_menu(),
            )

    elif action == "reports":
        from strix_telegram_bot.ui.messages import reports_menu_text
        edit_message(
            bot, chat_id, msg_id,
            "Reports coming in Phase 2.",
            reply_markup=back_to_menu(),
        )

    elif action == "caido":
        from strix_telegram_bot.ui.messages import caido_panel_text
        from strix_telegram_bot.jobs.job_runner import get_job_runner  # will need
        text = caido_panel_text(None, False)
        edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())


def _list_jobs(bot, chat_id, msg_id=None) -> None:
    store = JobStore()
    jobs = store.list_recent(limit=10)

    if not jobs:
        text = "No jobs yet."
        kb = back_to_menu()
    else:
        lines = ["Recent jobs:"]
        for j in jobs:
            icon = {
                "completed": "done",
                "failed": "fail",
                "stopped": "stop",
            }.get(j.phase.value, "active")
            lines.append(
                f"{icon} {escape_md(j.run_name[:30])} "
                f"[{j.mode.value}] {j.elapsed}"
            )
        text = "\n".join(lines)
        names = [j.run_name for j in jobs if j.run_name != "pending"]
        kb = active_jobs_list(names) if names else back_to_menu()

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
