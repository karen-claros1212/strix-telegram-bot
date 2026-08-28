"""Test jobs layer — JobStore persistence."""

from __future__ import annotations

from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobPhase, JobState


class TestJobStore:
    def test_save_and_get(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        job = JobState(run_name="test-run", target=["x"])
        store.save(job)

        retrieved = store.get("test-run")
        assert retrieved is not None
        assert retrieved.run_name == "test-run"
        assert retrieved.target == ["x"]

    def test_list_active(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        active = JobState(run_name="active-run", target=["x"], phase=JobPhase.SCANNING)
        done = JobState(run_name="done-run", target=["x"], phase=JobPhase.COMPLETED)
        store.save(active)
        store.save(done)

        active_list = store.list_active()
        assert len(active_list) == 1
        assert active_list[0].run_name == "active-run"

    def test_list_recent(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        for i in range(5):
            store.save(JobState(run_name=f"run-{i}", target=["x"]))
        recent = store.list_recent(limit=3)
        assert len(recent) == 3


class TestCmdStop:
    """FASE 5: cmd_stop is non-blocking (async) and honest ('Deteniendo...')."""

    def test_cmd_stop_running_sends_deteniendo_and_uses_async(self):
        from unittest.mock import MagicMock, patch

        from strix_telegram_bot.commands import jobs as jobs_mod

        bot = MagicMock()
        bridge = MagicMock()
        bridge.is_running = True
        bot._bridge = bridge
        update = {"message": {"chat": {"id": 123}}}

        with patch.object(jobs_mod, "send_message") as mock_send:
            jobs_mod.cmd_stop(bot, update)

        sent = mock_send.call_args
        assert "Deteniendo escaneo" in sent[0][2]
        bridge.stop_scan_async.assert_called_once()
        bridge.stop_scan.assert_not_called()

    def test_cmd_stop_not_running(self):
        from unittest.mock import MagicMock, patch

        from strix_telegram_bot.commands import jobs as jobs_mod

        bot = MagicMock()
        bridge = MagicMock()
        bridge.is_running = False
        bot._bridge = bridge
        update = {"message": {"chat": {"id": 123}}}

        with patch.object(jobs_mod, "send_message") as mock_send:
            jobs_mod.cmd_stop(bot, update)

        sent = mock_send.call_args
        assert "No hay escaneo activo" in sent[0][2]
        bridge.stop_scan_async.assert_not_called()



