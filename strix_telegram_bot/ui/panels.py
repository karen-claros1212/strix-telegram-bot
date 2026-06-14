from __future__ import annotations

from typing import Optional

from strix_telegram_bot.models import (
    FocusPreset,
    MenuState,
    ProfileType,
    ScanMode,
    ScopeMode,
    TargetType,
    JobState,
)


class PanelManager:
    def __init__(self) -> None:
        self._menu_stack: list[MenuState] = [MenuState.MAIN]
        self._selected_target_type: Optional[TargetType] = None
        self._selected_targets: list[str] = []
        self._selected_depth: ScanMode = ScanMode.DEEP
        self._selected_profile: ProfileType = ProfileType.INTERACTIVE
        self._selected_scope_mode: ScopeMode = ScopeMode.AUTO
        self._selected_diff_base: str = ""
        self._selected_focus: Optional[FocusPreset] = None
        self._selected_instruction: str = ""

    @property
    def current(self) -> MenuState:
        return self._menu_stack[-1] if self._menu_stack else MenuState.MAIN

    def push(self, state: MenuState) -> None:
        self._menu_stack.append(state)

    def pop(self) -> Optional[MenuState]:
        if len(self._menu_stack) > 1:
            return self._menu_stack.pop()
        return None

    def back_to_main(self) -> None:
        self._menu_stack = [MenuState.MAIN]

    def reset_wizard(self) -> None:
        self._selected_target_type = None
        self._selected_targets = []
        self._selected_depth = ScanMode.DEEP
        self._selected_profile = ProfileType.INTERACTIVE
        self._selected_scope_mode = ScopeMode.AUTO
        self._selected_diff_base = ""
        self._selected_focus = None
        self._selected_instruction = ""

    @property
    def wizard_complete(self) -> bool:
        return bool(
            self._selected_targets
            and self._selected_depth
        )

    def wizard_summary(self) -> str:
        parts = []
        if self._selected_targets:
            parts.append(f"Objetivo: {', '.join(self._selected_targets)}")
        if self._selected_depth:
            names = {"quick": "Rápido", "standard": "Estándar", "deep": "Profundo"}
            parts.append(f"Modo: {names.get(self._selected_depth.value, self._selected_depth.value)}")
        if self._selected_profile:
            parts.append(f"Perfil: {self._selected_profile.value}")
        if self._selected_scope_mode:
            parts.append(f"Alcance: {self._selected_scope_mode.value}")
        if self._selected_instruction:
            inst = (
                self._selected_instruction[:50] + "..."
                if len(self._selected_instruction) > 50
                else self._selected_instruction
            )
            parts.append(f"Instrucción: {inst}")
        return "\n".join(parts)


_panel_managers: dict[int, PanelManager] = {}


def get_panel_manager(chat_id: int = 0) -> PanelManager:
    if chat_id not in _panel_managers:
        _panel_managers[chat_id] = PanelManager()
    return _panel_managers[chat_id]
