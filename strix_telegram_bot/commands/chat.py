from __future__ import annotations

import logging
from typing import Any

from strix_telegram_bot.telegram import send_message, edit_message
from strix_telegram_bot.ui.keyboards import (
    main_menu,
    agent_selector,
    chat_connected,
    back_from_chat,
    parse_callback,
)
from strix_telegram_bot.ui.messages import escape_md
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import MenuState
from strix_telegram_bot.state.chat_session import get_chat_session
logger = logging.getLogger("strix_chat")



def cmd_chat(bot: Any, update: dict) -> None:
    chat_id = _chat_id(update)
    user_id = str(update.get("message", {}).get("from", {}).get("id", ""))
    msg_id = update.get("message", {}).get("message_id")

    session = get_chat_session(chat_id, user_id)

    if session.is_chat_active():
        session.exit_chat()
        pm = get_panel_manager(chat_id)
        pm.back_to_main()
        text = "Saliste del modo Chat."
        send_message(bot, chat_id, text, reply_markup=main_menu())
        return

    bridge = getattr(bot, "_bridge", None)
    if bridge and bridge.is_running:
        _enter_chat_with_bridge(bot, chat_id, user_id, bridge, msg_id)
        return

    from strix_telegram_bot.jobs.job_store import JobStore
    store = JobStore()
    active = store.list_active()
    if active:
        pm = get_panel_manager(chat_id)
        pm.back_to_main()
        from .jobs import cmd_status
        cmd_status(bot, update)
        return

    text = (
        "No hay una sesión interactiva disponible.\n"
            "Inicia un escaneo primero con /start o el botón Escanear."
    )
    send_message(bot, chat_id, text, reply_markup=main_menu())



def callback_chat(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    user_id = str(cb.get("from", {}).get("id", ""))
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "enter":
        bridge = getattr(bot, "_bridge", None)
        if bridge and bridge.is_running:
            _enter_chat_with_bridge(bot, chat_id, user_id, bridge, msg_id)
        else:
            edit_message(
                bot, chat_id, msg_id,
                "No hay escaneo activo para entrar en chat.",
                reply_markup=main_menu(),
            )

    elif action == "exit":
        session = get_chat_session(chat_id, user_id)
        session.exit_chat()
        pm = get_panel_manager(chat_id)
        pm.back_to_main()
        edit_message(
            bot, chat_id, msg_id,
            "Saliste del modo Chat.",
            reply_markup=main_menu(),
        )

    elif action == "agents":
        bridge = getattr(bot, "_bridge", None)
        if not bridge:
            edit_message(bot, chat_id, msg_id, "Bridge no disponible.", reply_markup=main_menu())
            return
        agents = bridge.list_agents()
        if not agents:
            edit_message(bot, chat_id, msg_id, "No hay agentes disponibles.", reply_markup=back_from_chat())
            return
        edit_message(
            bot, chat_id, msg_id,
            "Selecciona un agente:",
            reply_markup=agent_selector(agents),
        )



def callback_agent_select(bot: Any, update: dict) -> None:
    cb = update.get("callback_query", {})
    data = cb.get("data", "")
    chat_id = cb.get("message", {}).get("chat", {}).get("id", "")
    msg_id = cb.get("message", {}).get("message_id", "")
    user_id = str(cb.get("from", {}).get("id", ""))
    parts = parse_callback(data)

    if len(parts) < 2:
        return

    agent_id = parts[1]

    bridge = getattr(bot, "_bridge", None)
    if not bridge:
        edit_message(bot, chat_id, msg_id, "Bridge no disponible.", reply_markup=main_menu())
        return

    agents = bridge.list_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        edit_message(bot, chat_id, msg_id, "Agente no encontrado.", reply_markup=back_from_chat())
        return

    session = get_chat_session(chat_id, user_id)
    if not session.is_chat_active():
        run_name = bridge.run_name or "unknown"
        session.enter_chat(run_name, agent_id)
    else:
        session.selected_agent_id = agent_id

    pm = get_panel_manager(chat_id)
    pm.push(MenuState.CHAT)

    name = agent.get("name", agent_id)
    status = agent.get("status", "unknown")
    edit_message(
        bot, chat_id, msg_id,
        f"Conversando con: {escape_md(name)}\n"
        f"Estado: {escape_md(status)}\n\n"
        "Envía cualquier mensaje para hablar con el agente.",
        reply_markup=chat_connected(name, status),
    )


def _enter_chat_with_bridge(
    bot: Any, chat_id: int, user_id: str,
    bridge: Any, msg_id: int | None = None,
) -> None:
    agents = bridge.list_agents()
    if not agents:
        text = "El escaneo activo no reporta agentes todavía."
        if msg_id:
            edit_message(bot, chat_id, msg_id, text, reply_markup=main_menu())
        else:
            send_message(bot, chat_id, text, reply_markup=main_menu())
        return

    run_name = bridge.run_name or "unknown"

    if len(agents) == 1:
        agent = agents[0]
        session = get_chat_session(chat_id, user_id)
        session.enter_chat(run_name, agent["id"])
        pm = get_panel_manager(chat_id)
        pm.push(MenuState.CHAT)
        name = agent.get("name", agent["id"])
        status = agent.get("status", "unknown")
        text = (
            f"Modo Chat activo.\n"
            f"Conversando con: {escape_md(name)}\n"
            f"Estado: {escape_md(status)}\n\n"
            "Envía cualquier mensaje para hablar con el agente."
        )
        if msg_id:
            edit_message(bot, chat_id, msg_id, text, reply_markup=chat_connected(name, status))
        else:
            send_message(bot, chat_id, text, reply_markup=chat_connected(name, status))
        return

    text = "Hay múltiples agentes. Selecciona uno:"
    if msg_id:
        edit_message(bot, chat_id, msg_id, text, reply_markup=agent_selector(agents))
    else:
        send_message(bot, chat_id, text, reply_markup=agent_selector(agents))


def _chat_id(update: dict) -> int:
    return (
        update.get("message", {}).get("chat", {}).get("id", "")
        or update.get("callback_query", {})
        .get("message", {})
        .get("chat", {})
        .get("id", 0)
    )
