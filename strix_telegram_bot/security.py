from __future__ import annotations

from typing import Optional

from strix_telegram_bot.config import settings


class AccessPolicy:
    def __init__(self) -> None:
        self._allowed_users = settings.allowed_users
        self._allowed_chats = settings.allowed_chats

    def is_authorized(self, user_id: str, chat_id: str) -> bool:
        if not self._allowed_users and not self._allowed_chats:
            return True
        if user_id in self._allowed_users:
            return True
        if chat_id in self._allowed_chats:
            return True
        return False

    def authorized_only(self, func):
        def wrapper(bot, update, *args, **kwargs):
            uid = str(update.get("from", {}).get("id", ""))
            cid = str(
                update.get("chat", {}).get("id", "")
                or update.get("message", {})
                .get("chat", {})
                .get("id", "")
                or update.get("callback_query", {})
                .get("message", {})
                .get("chat", {})
                .get("id", "")
            )
            if not self.is_authorized(uid, cid):
                return None
            return func(bot, update, *args, **kwargs)

        return wrapper


_access = AccessPolicy()
authorized_only = _access.authorized_only
is_authorized = _access.is_authorized
