"""Test jobs layer — JobStore persistence."""

from __future__ import annotations

from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobState, JobPhase


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



