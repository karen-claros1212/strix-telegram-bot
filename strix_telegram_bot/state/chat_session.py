from __future__ import annotations

from strix_telegram_bot.models import MenuState


class ChatSession:
    def __init__(self, chat_id: int, user_id: str) -> None:
        self.chat_id = chat_id
        self.user_id = user_id
        self.mode: MenuState = MenuState.MAIN
        self.run_name: str | None = None
        self.selected_agent_id: str | None = None
        self._seen_event_ids: set[str] = set()

    def is_chat_active(self) -> bool:
        return self.mode == MenuState.CHAT

    def enter_chat(self, run_name: str, agent_id: str | None) -> None:
        self.mode = MenuState.CHAT
        self.run_name = run_name
        self.selected_agent_id = agent_id
        self._seen_event_ids.clear()

    def exit_chat(self) -> None:
        self.mode = MenuState.MAIN
        self.selected_agent_id = None
        self._seen_event_ids.clear()


_sessions: dict[tuple[int, str], ChatSession] = {}


def get_chat_session(chat_id: int, user_id: str) -> ChatSession:
    key = (chat_id, user_id)
    if key not in _sessions:
        _sessions[key] = ChatSession(chat_id, user_id)
    return _sessions[key]


def clear_chat_session(chat_id: int, user_id: str) -> None:
    _sessions.pop((chat_id, user_id), None)


def get_all_chat_sessions() -> list[ChatSession]:
    return list(_sessions.values())
