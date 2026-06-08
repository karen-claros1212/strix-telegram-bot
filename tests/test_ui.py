"""Test UI components — keyboards, messages, panels."""

from __future__ import annotations

from strix_telegram_bot.ui.keyboards import (
    main_menu,
    target_type_selector,
    depth_selector,
    job_panel,
    approval_keyboard,
    parse_callback,
)
from strix_telegram_bot.ui.messages import (
    main_menu_text,
    job_status_text,
    health_text,
    help_text,
    escape_md,
)
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import JobState, JobPhase, ScanMode, MenuState


class TestKeyboards:
    def test_main_menu_structure(self):
        kb = main_menu()
        assert "inline_keyboard" in kb
        assert len(kb["inline_keyboard"]) >= 3

    def test_target_selector(self):
        kb = target_type_selector()
        rows = kb["inline_keyboard"]
        texts = [b["text"] for row in rows for b in row]
        assert "URL / Domain" in texts
        assert "Back" in texts

    def test_depth_selector(self):
        kb = depth_selector()
        rows = kb["inline_keyboard"]
        texts = [b["text"] for row in rows for b in row]
        assert "Quick" in texts
        assert "Deep" in texts
        assert "Continue" in texts

    def test_job_panel_has_stop(self):
        kb = job_panel(running=True)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "STOP" in texts

    def test_job_panel_no_stop(self):
        kb = job_panel(running=False)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "STOP" not in texts

    def test_approval_keyboard(self):
        kb = approval_keyboard()
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Authorize" in texts
        assert "Cancel" in texts

    def test_parse_callback(self):
        parts = parse_callback("menu:new_pentest")
        assert parts == ("menu", "new_pentest")

        parts = parse_callback("depth:deep")
        assert parts == ("depth", "deep")


class TestMessages:
    def test_main_menu_text(self):
        text = main_menu_text()
        assert "STRIX" in text
        assert "Control Center" in text

    def test_job_status(self):
        job = JobState(
            run_name="test", target=["https://example.com"],
            phase=JobPhase.SCANNING, mode=ScanMode.DEEP,
        )
        text = job_status_text(job)
        assert "Scan" in text
        assert "scanning" in text

    def test_health_text(self):
        text = health_text("1.0.2", "3.12.0", "1h 30m", 2, "Active")
        assert "1\\.0\\.2" in text or "1.0.2" in text
        assert "3\\.12\\.0" in text or "3.12.0" in text

    def test_help_text(self):
        text = help_text()
        assert "New Pentest" in text
        assert "Quick" in text
        assert "Deep" in text

    def test_escape_md(self):
        result = escape_md("hello_world")
        assert r"hello\_world" == result


class TestPanelManager:
    def test_navigation(self):
        pm = get_panel_manager()
        pm.back_to_main()
        assert pm.current.name == "MAIN"

        pm.push(MenuState.NEW_PENTEST_TARGET)
        assert pm.current.name == "NEW_PENTEST_TARGET"

        pm.pop()
        assert pm.current.name == "MAIN"

    def test_wizard(self):
        pm = get_panel_manager()
        pm.reset_wizard()
        assert pm.wizard_complete is False

        pm._selected_targets = ["https://example.com"]
        pm._selected_depth = ScanMode.QUICK
        assert pm.wizard_complete is True
