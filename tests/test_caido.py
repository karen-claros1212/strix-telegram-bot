"""Test Caido Panel detection and artifacts."""

from __future__ import annotations

from strix_telegram_bot.strix.caido_panel import CaidoPanel


class TestCaidoPanel:
    def test_initial_state(self):
        cp = CaidoPanel()
        assert cp.url is None
        assert cp.port is None
        assert cp.active is False

    def test_update_from_text_url(self):
        cp = CaidoPanel()
        url = cp.update_from_text("Caido proxy at http://caido.local:8080")
        assert url == "http://caido.local:8080"
        assert cp.active is True

    def test_update_from_text_port(self):
        cp = CaidoPanel()
        url = cp.update_from_text("Caido listening on port 8080")
        assert cp.port == 8080
        assert cp.active is True

    def test_update_from_text_port_only(self):
        cp = CaidoPanel()
        cp.update_from_text("Proxy port 9090")
        assert cp.port == 9090
        assert cp.active is True
        assert cp.url is not None

    def test_clear(self):
        cp = CaidoPanel()
        cp.set_url("http://caido.local:8080")
        cp.set_active(True)
        cp.clear()
        assert cp.url is None
        assert cp.port is None
        assert cp.active is False

    def test_status_line_active(self):
        cp = CaidoPanel()
        cp.set_url("http://127.0.0.1:8080")
        cp.set_active(True)
        assert "Active" in cp.status_line()

    def test_status_line_active_port_only(self):
        cp = CaidoPanel()
        cp._port = 8080
        cp._active = True
        assert "port" in cp.status_line()

    def test_status_line_inactive(self):
        cp = CaidoPanel()
        assert "Inactive" in cp.status_line()

    def test_build_caido_panel_inactive(self):
        cp = CaidoPanel()
        panel = cp.build_caido_panel("")
        assert "Inactive" in panel

    def test_build_caido_panel_active(self, tmp_path):
        cp = CaidoPanel()
        cp.set_url("http://127.0.0.1:8080")
        cp.set_active(True)

        runs_dir = tmp_path / "strix_runs" / "test-run"
        runs_dir.mkdir(parents=True)

        panel = cp.build_caido_panel("test-run")
        assert "Active" in panel
        assert "http://127.0.0.1:8080" in panel

    def test_collect_caido_artifacts(self, tmp_path):
        caido_dir = tmp_path / "strix_runs" / "test-run" / "caido"
        caido_dir.mkdir(parents=True)
        (caido_dir / "capture.json").write_text("{}")

        cp = CaidoPanel()
        artifacts = cp.collect_caido_artifacts("test-run")
        assert len(artifacts) == 0

        from strix_telegram_bot.config import settings
        old_dir = settings.strix_runs_dir
        settings.strix_runs_dir = tmp_path / "strix_runs"

        try:
            artifacts = cp.collect_caido_artifacts("test-run")
            assert len(artifacts) == 1
        finally:
            settings.strix_runs_dir = old_dir
