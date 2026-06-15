from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import MenuState


class PanelManager:
    def __init__(self) -> None:
        self._menu_stack: list[MenuState] = [MenuState.MAIN]

    @property
    def current(self) -> MenuState:
        return self._menu_stack[-1] if self._menu_stack else MenuState.MAIN

    def push(self, state: MenuState) -> None:
        self._menu_stack.append(state)

    def back_to_main(self) -> None:
        self._menu_stack = [MenuState.MAIN]


_panel_managers: dict[int, PanelManager] = {}


def get_panel_manager(chat_id: int = 0) -> PanelManager:
    if chat_id not in _panel_managers:
        _panel_managers[chat_id] = PanelManager()
    return _panel_managers[chat_id]
