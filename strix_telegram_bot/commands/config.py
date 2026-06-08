from __future__ import annotations

from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message, answer_callback
from strix_telegram_bot.ui.keyboards import config_menu, back_to_menu, parse_callback
from strix_telegram_bot.ui.messages import config_text, escape_md
from strix_telegram_bot.config import settings
from strix_telegram_bot.security import authorized_only


@authorized_only
def cmd_config(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    _show_config(bot, chat_id)


@authorized_only
def callback_config(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    parts = parse_callback(data)

    answer_callback(bot, cb.get("id", ""))

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "menu":
        _show_config(bot, chat_id, msg_id)

    elif action == "mode":
        edit_message(
            bot, chat_id, msg_id,
            "Default scan mode.\n"
            "Use New Pentest to select mode per scan.",
            reply_markup=back_to_menu(),
        )

    elif action == "scope":
        edit_message(
            bot, chat_id, msg_id,
            "Scope mode controls how targets are analyzed.\n"
            "auto: diff-scope in CI, full otherwise\n"
            "diff: changed-files only\n"
            "full: no diff-scope",
            reply_markup=back_to_menu(),
        )

    elif action == "llm":
        edit_message(
            bot, chat_id, msg_id,
            f"Current LLM: {escape_md(settings.llm_model)}",
            reply_markup=back_to_menu(),
        )

    elif action == "users":
        users = ", ".join(settings.allowed_users) if settings.allowed_users else "all"
        edit_message(
            bot, chat_id, msg_id,
            f"Allowed users: {escape_md(users)}",
            reply_markup=back_to_menu(),
        )


def _show_config(bot, chat_id, msg_id=None) -> None:
    d = {
        "LLM Model": settings.llm_model,
        "Allowed Users": ", ".join(settings.allowed_users) if settings.allowed_users else "all",
        "Allowed Chats": ", ".join(settings.allowed_chats) if settings.allowed_chats else "all",
        "API Token": "***" + settings.tg_token[-4:] if settings.tg_token else "not set",
    }
    text = config_text(d)
    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=config_menu())
    else:
        send_message(bot, chat_id, text, reply_markup=config_menu())


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
