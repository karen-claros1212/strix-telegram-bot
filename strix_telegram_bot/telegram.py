"""Raw HTTP Telegram Bot API wrapper (stdlib-only)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .config import settings

_API_BASE = settings.api_base
_RETRY_DELAY = 0.5
_MAX_RETRIES = 3

logger = logging.getLogger("strix_telegram")


def _api_url(method: str) -> str:
    return f"{_API_BASE}/{method}"


def _request(
    method: str,
    payload: Optional[dict] = None,
    retries: int = _MAX_RETRIES,
    request_timeout: int = 30,
) -> Optional[dict]:
    url = _api_url(method)
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json"} if data else {}

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                body = resp.read().decode()
                result = json.loads(body)
                if result.get("ok"):
                    return result.get("result")
                logger.warning(
                    "Telegram API error [%s]: %s — %s",
                    method, result.get("error_code", "?"), result.get("description", "?"),
                )
            return None
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            # Don't retry on permanent errors
            if "message is not modified" in body or "message to edit not found" in body or "message can't be edited" in body:
                return None
            logger.warning(
                "HTTP %d on %s (attempt %d/%d): %s",
                e.code, method, attempt + 1, retries, e.reason,
            )
            if attempt < retries - 1:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue
            return None
        except (urllib.error.URLError, OSError) as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            # Don't retry on permanent network failures
            if "Network is unreachable" in reason or "Name or service not known" in reason:
                return None
            logger.warning(
                "Connection error on %s (attempt %d/%d): %s",
                method, attempt + 1, retries, reason,
            )
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
    result = _request(
        "getUpdates",
        payload,
        retries=1,
        request_timeout=timeout + 10,
    )
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
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _request("sendMessage", payload)
    if result:
        return result
    # Markdown fallback: retry without parse_mode
    if parse_mode:
        payload.pop("parse_mode", None)
        result = _request("sendMessage", payload)
    return result


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
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _request("editMessageText", payload)
    if result:
        return result
    # Markdown fallback: retry without parse_mode
    if parse_mode:
        payload.pop("parse_mode", None)
        result = _request("editMessageText", payload)
    return result


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


def get_file(bot: Any, file_id: str) -> Optional[bytes]:
    result = _request("getFile", {"file_id": file_id})
    if not result or "file_path" not in result:
        return None
    file_path = result["file_path"]
    file_url = f"{_API_BASE.replace('/bot', '/file/bot')}/{file_path}"
    try:
        req = urllib.request.Request(file_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def send_chat_action(bot: Any, chat_id: int, action: str = "typing") -> Optional[dict]:
    return _request("sendChatAction", {
        "chat_id": chat_id,
        "action": action,
    })
