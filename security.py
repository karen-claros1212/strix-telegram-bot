from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccessPolicy:
    allowed_users: set[int]
    allowed_chats: set[int]

    def is_allowed(self, user_id: int, chat_id: int) -> bool:
        if user_id not in self.allowed_users:
            return False
        if self.allowed_chats and chat_id not in self.allowed_chats:
            return False
        return True
