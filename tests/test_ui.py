from strix_telegram_bot.ui.keyboards import (
    main_menu,
    job_panel,
    parse_callback,
    back_to_menu,
)
from strix_telegram_bot.ui.messages import (
    main_menu_text,
    job_status_text,
    health_text,
    help_text,
    escape_md,
)
from strix_telegram_bot.ui.panels import get_panel_manager
from strix_telegram_bot.models import MenuState


class TestKeyboards:
    def test_main_menu_structure(self):
        kb = main_menu()
        assert "inline_keyboard" in kb
        assert len(kb["inline_keyboard"]) >= 1
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Escanear" in texts

    def test_back_to_menu(self):
        kb = back_to_menu()
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Volver al menú" in texts

    def test_job_panel_has_stop(self):
        kb = job_panel(running=True)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Detener" in texts
        assert "Estado" not in texts

    def test_job_panel_no_stop(self):
        kb = job_panel(running=False)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Detener" not in texts
        assert "Estado" not in texts

    def test_parse_callback(self):
        parts = parse_callback("menu:scan")
        assert parts == ("menu", "scan")


class TestMessages:
    def test_main_menu_text(self):
        text = main_menu_text()
        assert "STRIX" in text
        assert "Centro de Control" in text

    def test_job_status_from_dict(self):
        status = {
            "run_name": "test",
            "target": ["https://example.com"],
            "phase": "running",
            "mode": "deep",
            "elapsed": "10s",
            "is_active": True,
        }
        text = job_status_text(status)
        assert "STRIX" in text
        assert "Ejecutando" in text

    def test_job_status_with_agents(self):
        status = {
            "run_name": "test",
            "target": ["https://example.com"],
            "phase": "running",
            "mode": "deep",
            "elapsed": "10s",
            "is_active": True,
        }
        tool_state = {
            "current_tool_name": "nuclei",
            "current_tool_args": {"target": "example.com"},
            "current_tool_status": "running",
            "active_count": 1,
            "completed_count": 3,
            "failed_count": 0,
            "active_agent_name": "",
        }
        text = job_status_text(status, tool_state=tool_state)
        assert "Nuclei" in text
        assert "Buscando vulnerabilidades" in text
        assert "3 completadas" in text

    def test_job_status_initializing(self):
        status = {
            "phase": "initializing",
            "mode": "deep",
            "elapsed": "0s",
            "is_active": True,
        }
        text = job_status_text(status)
        assert "Inicializando" in text

    def test_health_text(self):
        text = health_text("1.0.2", "3.12.0", "1h 30m", 2, "Active")
        assert "1\\.0\\.2" in text or "1.0.2" in text

    def test_help_text(self):
        text = help_text()
        assert "/status" in text
        assert "Escanear" in text

    def test_escape_md(self):
        result = escape_md("hello_world")
        assert r"hello\_world" == result


class TestPanelManager:
    def test_navigation(self):
        pm = get_panel_manager()
        pm.back_to_main()
        assert pm.current.name == "MAIN"

        pm.push(MenuState.WAITING_FOR_TARGETS)
        assert pm.current.name == "WAITING_FOR_TARGETS"

        pm.back_to_main()
        assert pm.current.name == "MAIN"
