"""Raw HTTP Telegram Bot API wrapper (stdlib-only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .config import settings

_API_BASE = settings.api_base
_RETRY_DELAY = 0.5
_MAX_RETRIES = 3


def _api_url(method: str) -> str:
    return f"{_API_BASE}/{method}"


def _request(
    method: str,
    payload: Optional[dict] = None,
    retries: int = _MAX_RETRIES,
) -> Optional[dict]:
    url = _api_url(method)
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json"} if data else {}

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
                result = json.loads(body)
                if result.get("ok"):
                    return result.get("result")
            return None
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue
            return None
    return None


def get_updates(offset: Optional[int] = None, timeout: int = 30) -> list[dict]:
    payload: dict[str, Any] = {
        "timeout": timeout,
        "allowed_updates": [
            "message",
            "callback_query",
            "my_chat_member",
        ],
    }
    if offset is not None:
        payload["offset"] = offset
    result = _request("getUpdates", payload)
    return result if result else []


def send_message(
    bot: Any,
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    reply_markup: Optional[dict] = None,
    disable_web_page_preview: bool = True,
) -> Optional[dict]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _request("sendMessage", payload)


def edit_message(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: str = "Markdown",
    reply_markup: Optional[dict] = None,
) -> Optional[dict]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _request("editMessageText", payload)


def delete_message(bot: Any, chat_id: int, message_id: int) -> Optional[dict]:
    return _request("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })


def answer_callback(bot: Any, callback_id: str, text: str = "") -> Optional[dict]:
    payload: dict[str, Any] = {
        "callback_query_id": callback_id,
    }
    if text:
        payload["text"] = text
    return _request("answerCallbackQuery", payload)


def send_chat_action(bot: Any, chat_id: int, action: str = "typing") -> Optional[dict]:
    return _request("sendChatAction", {
        "chat_id": chat_id,
        "action": action,
    })
