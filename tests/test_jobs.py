"""Test jobs layer — store, process control, basic runner state."""

from __future__ import annotations

from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.jobs.process_control import ProcessController
from strix_telegram_bot.models import JobState, JobPhase, ScanMode


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


class TestProcessController:
    def test_register_and_unregister(self):
        ctrl = ProcessController()
        ctrl.register("test", 99999)
        assert ctrl.get_pid("test") == 99999

        ctrl.unregister("test")
        assert ctrl.get_pid("test") is None

    def test_is_alive_nonexistent(self):
        ctrl = ProcessController()
        assert ctrl.is_alive("nothing") is False

    def test_stop_nonexistent(self):
        ctrl = ProcessController()
        assert ctrl.stop("nothing") is False
