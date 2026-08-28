from strix_telegram_bot.models import MenuState
from strix_telegram_bot.ui.keyboards import (
    back_to_menu,
    job_panel,
    main_menu,
    parse_callback,
    report_detail_menu,
    reports_main_menu,
    scan_mode_menu,
)
from strix_telegram_bot.ui.messages import (
    escape_md,
    health_text,
    help_text,
    job_status_text,
    main_menu_text,
)
from strix_telegram_bot.ui.panels import get_panel_manager


class TestKeyboards:
    def test_main_menu_structure(self):
        kb = main_menu()
        assert "inline_keyboard" in kb
        assert len(kb["inline_keyboard"]) >= 1
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Escanear" in texts

    def test_scan_mode_menu(self):
        kb = scan_mode_menu()
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Quick" in texts
        assert "Standard" in texts
        assert "Deep" in texts

    def test_back_to_menu(self):
        kb = back_to_menu()
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Volver al menú" in texts

    def test_job_panel_has_chat(self):
        kb = job_panel(running=True)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Chat" in texts

    def test_job_panel_has_stop(self):
        kb = job_panel(running=True)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Detener" in texts

    def test_job_panel_no_stop(self):
        kb = job_panel(running=False)
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Detener" not in texts
        assert "Estado" not in texts

    def test_parse_callback(self):
        parts = parse_callback("menu:scan")
        assert parts == ("menu", "scan")

    def test_reports_main_menu_exists(self):
        kb = reports_main_menu()
        texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert "Último reporte" in texts
        assert "Historial" in texts
        assert "Resumen ejecutivo" in texts
        assert "Limpiar viejos" in texts

    def test_report_detail_menu_download_includes_run_name(self):
        run_name = "scan-e2b8cca0"
        kb = report_detail_menu(run_name)
        cbs = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert f"report:download_md:{run_name}" in cbs
        assert all(len(cb) <= 64 for cb in cbs)


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
            "streaming": False,
            "awaiting_input": False,
            "input_prompt": "",
        }
        text = job_status_text(status, tool_state=tool_state)
        assert "Nuclei" in text
        assert "Buscando vulnerabilidades" in text
        assert "3 completadas" in text

    def test_job_status_awaiting(self):
        status = {
            "phase": "running",
            "mode": "deep",
            "elapsed": "2m",
            "is_active": True,
        }
        # Awaiting with no real prompt: shows generic message
        tool_state = {
            "current_tool_name": "",
            "streaming": False,
            "awaiting_input": True,
            "input_prompt": "",
            "active_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "active_agent_name": "",
        }
        text = job_status_text(status, tool_state=tool_state)
        assert "Disponible para recibir instrucciones" in text

    def test_job_status_streaming(self):
        status = {
            "phase": "running",
            "mode": "deep",
            "elapsed": "1m",
            "is_active": True,
        }
        tool_state = {
            "current_tool_name": "",
            "streaming": True,
            "awaiting_input": False,
            "input_prompt": "",
            "active_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "active_agent_name": "",
        }
        text = job_status_text(status, tool_state=tool_state)
        assert "Redactando una respuesta" in text

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
