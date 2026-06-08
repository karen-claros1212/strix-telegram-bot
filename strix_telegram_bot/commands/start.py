from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import main_menu, parse_callback
from strix_telegram_bot.ui.messages import main_menu_text, help_text
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import MenuState
from strix_telegram_bot.security import authorized_only


@authorized_only
def cmd_start(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
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

    pm = get_panel_manager()

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
        from strix_telegram_bot.ui.keyboards import target_type_selector
        edit_message(
            bot, chat_id, msg_id,
            "Select target type:",
            reply_markup=target_type_selector(),
        )

    elif action == "chat":
        edit_message(
            bot, chat_id, msg_id,
            "Send a message to interact with STRIX.\n\n"
            "If a job is running and waiting for input, "
            "your message will be sent as a response.",
            reply_markup=main_menu(),
        )

    elif action == "jobs":
        _show_jobs(bot, chat_id, msg_id)

    elif action == "reports":
        from strix_telegram_bot.commands.reports import _show_reports
        _show_reports(bot, chat_id, msg_id)

    elif action == "caido":
        _show_caido_panel(bot, chat_id, msg_id)

    elif action == "health":
        _show_health(bot, chat_id, msg_id)

    elif action == "config":
        pm.push(MenuState.CONFIG)
        from strix_telegram_bot.ui.keyboards import config_menu
        edit_message(
            bot, chat_id, msg_id,
            "Configuration:", reply_markup=config_menu(),
        )

    elif action == "help":
        edit_message(
            bot, chat_id, msg_id, help_text(),
            reply_markup=main_menu(),
        )

    answer_callback(bot, cb.get("id", ""))


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

    lines = ["Jobs Overview:"]
    if active:
        lines.append(f"Active: {len(active)}")
    lines.append(f"Recent: {len(all_jobs)}")

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
