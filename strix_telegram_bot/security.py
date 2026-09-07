from __future__ import annotations

from strix_telegram_bot.config import settings

# Chat types where the chat itself must also be allowlisted (not just the user).
_GROUP_TYPES = ("group", "supergroup")


class AccessPolicy:
    def __init__(self) -> None:
        self._allowed_users = settings.allowed_users
        self._allowed_chats = settings.allowed_chats

    def validate(self) -> list[str]:
        """Fail-closed startup validation. Returns a list of error strings.

        An empty user allowlist means the bot serves nobody (every check fails),
        so it is a configuration error that must stop the service at boot rather
        than silently running with an open or dead policy.
        """
        errors: list[str] = []
        if not self._allowed_users:
            errors.append(
                "STRIX_TG_ALLOWED_USERS vacía: sin usuarios autorizados el bot "
                "no sirve a nadie (fail-closed)"
            )
        return errors

    def is_authorized(self, user_id: str, chat_id: str, chat_type: str = "") -> bool:
        # The user must always be allowlisted (fail-closed: unknown user = denied).
        if user_id not in self._allowed_users:
            return False
        # In groups/supergroups the chat must ALSO be allowlisted, so a single
        # allowed user cannot reach the bot from any arbitrary group.
        if chat_type in _GROUP_TYPES:
            return chat_id in self._allowed_chats
        return True

    def authorized_only(self, func):
        def wrapper(bot, update, *args, **kwargs):
            uid = str(update.get("from", {}).get("id", ""))
            chat = (
                update.get("message", {}).get("chat", {})
                or update.get("callback_query", {})
                .get("message", {})
                .get("chat", {})
                or update.get("chat", {})
            )
            cid = str(chat.get("id", ""))
            chat_type = chat.get("type", "")
            if not self.is_authorized(uid, cid, chat_type):
                return None
            return func(bot, update, *args, **kwargs)

        return wrapper


_access = AccessPolicy()
authorized_only = _access.authorized_only
is_authorized = _access.is_authorized
