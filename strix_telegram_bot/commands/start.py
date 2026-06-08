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
        from strix_telegram_bot.ui.messages import escape_md
        edit_message(
            bot, chat_id, msg_id,
            "Select target type:",
            reply_markup=target_type_selector(),
        )

    elif action == "chat":
        from strix_telegram_bot.ui.messages import escape_md
        edit_message(
            bot, chat_id, msg_id,
            "Send a message to interact with STRIX.\n\n"
            "If a job is running and waiting for input, "
            "your message will be sent as a response.",
            reply_markup=main_menu(),
        )

    elif action == "jobs":
        from strix_telegram_bot.ui.keyboards import back_to_menu
        _show_jobs(bot, chat_id, msg_id)

    elif action == "reports":
        from strix_telegram_bot.ui.keyboards import back_to_menu
        edit_message(
            bot, chat_id, msg_id,
            "Reports feature coming in Phase 2.",
            reply_markup=back_to_menu(),
        )

    elif action == "caido":
        from strix_telegram_bot.ui.messages import caido_panel_text
        from strix_telegram_bot.ui.keyboards import back_to_menu
        from strix_telegram_bot.jobs.job_runner import get_job_runner  # will need
        text = caido_panel_text(None, False)
        edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())

    elif action == "health":
        _show_health(bot, chat_id, msg_id)

    elif action == "config":
        pm.push(MenuState.CONFIG)
        from strix_telegram_bot.ui.keyboards import config_menu
        from strix_telegram_bot.ui.messages import escape_md
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
    from strix_telegram_bot.ui.keyboards import active_jobs_list, back_to_menu
    from strix_telegram_bot.jobs.job_store import JobStore
    from strix_telegram_bot.jobs.process_control import ProcessController
    from strix_telegram_bot.ui.messages import escape_md

    store = JobStore()
    active = store.list_active()
    if active:
        names = [j.run_name for j in active]
        names = [n for n in names if n != "pending"]
        if not names:
            text = "No named active jobs."
        else:
            text = "Active jobs:"
        edit_message(
            bot, chat_id, msg_id, text,
            reply_markup=active_jobs_list(names) if names else back_to_menu(),
        )
    else:
        edit_message(
            bot, chat_id, msg_id,
            "No active jobs.", reply_markup=back_to_menu(),
        )


def _show_health(bot, chat_id, msg_id) -> None:
    from strix_telegram_bot.ui.messages import health_text
    from strix_telegram_bot.ui.keyboards import back_to_menu
    from strix_telegram_bot.config import settings

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
    active_count = len(store.list_active())

    text = health_text(
        strix_version=ver,
        python_version=platform.python_version(),
        uptime="N/A",
        active_jobs=active_count,
        caido_status="N/A",
    )
    edit_message(bot, chat_id, msg_id, text, reply_markup=back_to_menu())
