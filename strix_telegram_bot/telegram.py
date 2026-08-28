"""Raw HTTP Telegram Bot API wrapper (stdlib-only)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .config import settings

_API_BASE = settings.api_base
_RETRY_DELAY = 0.5
_MAX_RETRIES = 3

logger = logging.getLogger("strix_telegram")


@dataclass(frozen=True)
class SendOutcome:
    """Typed result of a sendDocument call.

    kind is one of:
        "success"   — document delivered, .result holds the API result (message_id)
        "transient" — network/server hiccup, worth retrying later
        "permanent" — client/file error, retrying the same payload won't help
    """

    ok: bool
    kind: str
    result: Optional[dict] = None

    @property
    def message_id(self) -> Optional[int]:
        return self.result.get("message_id") if self.result else None

    @classmethod
    def success(cls, result: Optional[dict]) -> "SendOutcome":
        return cls(ok=True, kind="success", result=result)

    @classmethod
    def transient(cls) -> "SendOutcome":
        return cls(ok=False, kind="transient")

    @classmethod
    def permanent(cls) -> "SendOutcome":
        return cls(ok=False, kind="permanent")


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
                    "Telegram API error [%s]: %s — %s — payload: %s",
                    method, result.get("error_code", "?"), result.get("description", "?"),
                    json.dumps(payload, default=str) if payload else "none",
                )
            return None
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            # 400 errors are permanent — never retry
            if e.code == 400:
                logger.debug(
                    "HTTP 400 on %s (not retried): %s",
                    method, body[:300],
                )
                return None
            # Other permanent errors
            if (
                "message is not modified" in body
                or "message to edit not found" in body
                or "message can't be edited" in body
            ):
                return None
            logger.warning(
                "HTTP %d on %s (attempt %d/%d): %s — body: %s",
                e.code, method, attempt + 1, retries, e.reason, body[:200],
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
    parse_mode: Optional[str] = None,
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
    return result


def edit_message(
    bot: Any,
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[dict] = None,
    disable_web_page_preview: bool = False,
) -> Optional[dict]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
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


def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = name.replace("\r", "").replace("\n", "")
    name = name.replace('"', "").replace("'", "")
    safe = re.sub(r"[^\w.\- ]", "", name)
    safe = safe.strip()
    if not safe:
        safe = "report.md"
    if not safe.lower().endswith(".md"):
        safe += ".md"
    return safe


def _build_multipart_form(fields: dict, files: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data body and return (body, content_type)."""
    boundary = f"----strixFormBoundary{int(time.time() * 1_000_000)}"
    lines: list[bytes] = []

    for key, value in fields.items():
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        encoded = str(value).encode("utf-8")
        lines.append(encoded)
        lines.append(b"\r\n")

    for key, (filename, file_bytes, mime_type) in files.items():
        safe_name = _sanitize_filename(filename)
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{key}"; filename="{safe_name}"\r\n'.encode()
        )
        lines.append(f"Content-Type: {mime_type}\r\n\r\n".encode())
        lines.append(file_bytes)
        lines.append(b"\r\n")

    lines.append(f"--{boundary}--\r\n".encode())
    body = b"".join(lines)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _is_permanent_client_error(code: int) -> bool:
    return code in (400, 401, 403, 404, 405, 409, 413, 422)


def send_document(
    bot: Any,
    chat_id: int,
    file_path: str,
    filename: Optional[str] = None,
    caption: Optional[str] = None,
    reply_markup: Optional[dict] = None,
) -> SendOutcome:
    """Send a file as a document using Telegram's sendDocument API (multipart/form-data).

    Returns a SendOutcome whose .kind is "success", "transient", or "permanent".
    On success, .result holds the API result (with message_id). Never raises.
    """
    url = _api_url("sendDocument")

    if not os.path.isfile(file_path):
        logger.warning("send_document: file not found — %s", file_path)
        return SendOutcome.permanent()

    display_name = _sanitize_filename(filename or os.path.basename(file_path))

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except OSError as e:
        logger.error("send_document: cannot read %s — %s", file_path, e)
        return SendOutcome.permanent()

    if not file_bytes:
        logger.warning("send_document: empty file — %s", file_path)
        return SendOutcome.permanent()

    fields: dict[str, str] = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    files: dict = {
        "document": (display_name, file_bytes, "text/markdown"),
    }

    body, content_type = _build_multipart_form(fields, files)
    headers = {"Content-Type": content_type}
    req = urllib.request.Request(url, data=body, headers=headers)

    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                result = json.loads(raw)
                if result.get("ok"):
                    return SendOutcome.success(result.get("result"))
                error_code = result.get("error_code", 0)
                logger.warning(
                    "Telegram API error [sendDocument]: %s — %s",
                    error_code,
                    result.get("description", "?"),
                )
                if isinstance(error_code, int) and 400 <= error_code < 500:
                    return SendOutcome.permanent()
                return SendOutcome.transient()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if _is_permanent_client_error(e.code):
                logger.debug("HTTP %d on sendDocument (not retried): %s", e.code, err_body[:300])
                return SendOutcome.permanent()
            if e.code == 429:
                delay = _RETRY_DELAY * (2 ** attempt)
                try:
                    parsed = json.loads(err_body)
                    retry_after = int(parsed.get("parameters", {}).get("retry_after", delay))
                    delay = max(delay, retry_after)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
                logger.warning("HTTP 429 on sendDocument, retrying in %.1fs", delay)
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(delay)
                    continue
                return SendOutcome.transient()
            if e.code == 408 or e.code >= 500:
                logger.warning(
                    "HTTP %d on sendDocument (attempt %d/%d): retrying",
                    e.code, attempt + 1, _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (2 ** attempt))
                    continue
                return SendOutcome.transient()
            return SendOutcome.permanent()
        except (urllib.error.URLError, OSError) as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            if "Network is unreachable" in reason or "Name or service not known" in reason:
                logger.warning("send_document: network unreachable/DNS — %s", reason)
                return SendOutcome.transient()
            logger.warning(
                "Connection error on sendDocument (attempt %d/%d): %s",
                attempt + 1, _MAX_RETRIES, reason,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue
            return SendOutcome.transient()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            logger.error("send_document: invalid response — %s", e)
            return SendOutcome.transient()

    return SendOutcome.transient()
