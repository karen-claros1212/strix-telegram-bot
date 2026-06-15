from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message
from strix_telegram_bot.ui.keyboards import main_menu, parse_callback, back_to_menu
from strix_telegram_bot.ui.messages import main_menu_text, help_text
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import MenuState


def cmd_start(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    send_message(bot, chat_id, main_menu_text(), reply_markup=main_menu())


def cmd_help(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    send_message(bot, chat_id, help_text(), reply_markup=main_menu())


def callback_menu(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "main":
        pm = get_panel_manager(chat_id)
        pm.back_to_main()
        edit_message(
            bot, chat_id, msg_id, main_menu_text(),
            reply_markup=main_menu(),
        )

    elif action == "scan":
        pm = get_panel_manager(chat_id)
        pm.push(MenuState.WAITING_FOR_TARGETS)
        from strix_telegram_bot.ui.messages import waiting_for_targets_text
        edit_message(
            bot, chat_id, msg_id,
            waiting_for_targets_text(),
            reply_markup=back_to_menu(),
        )


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
