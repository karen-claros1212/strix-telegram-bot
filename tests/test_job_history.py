"""Test Job History — filtering, search, status queries."""

from __future__ import annotations

from strix_telegram_bot.jobs.job_store import JobStore
from strix_telegram_bot.models import JobState, JobPhase, ScanMode


class TestJobHistory:
    def test_list_by_status(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="run-a", target=["x"], phase=JobPhase.COMPLETED))
        store.save(JobState(run_name="run-b", target=["x"], phase=JobPhase.COMPLETED))
        store.save(JobState(run_name="run-c", target=["x"], phase=JobPhase.FAILED))
        store.save(JobState(run_name="run-d", target=["x"], phase=JobPhase.STOPPED))

        completed = store.list_by_status(JobPhase.COMPLETED)
        assert len(completed) == 2

        failed = store.list_by_status(JobPhase.FAILED)
        assert len(failed) == 1

        stopped = store.list_by_status(JobPhase.STOPPED)
        assert len(stopped) == 1

    def test_list_completed(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="a", target=["x"], phase=JobPhase.COMPLETED))
        store.save(JobState(run_name="b", target=["x"], phase=JobPhase.SCANNING))
        store.save(JobState(run_name="c", target=["x"], phase=JobPhase.COMPLETED))

        completed = store.list_completed()
        assert len(completed) == 2

    def test_list_failed(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="a", target=["x"], phase=JobPhase.FAILED))
        completed = store.list_failed()
        assert len(completed) == 1

    def test_list_stopped(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="a", target=["x"], phase=JobPhase.STOPPED))
        stopped = store.list_stopped()
        assert len(stopped) == 1

    def test_count_active(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="a", target=["x"], phase=JobPhase.SCANNING))
        store.save(JobState(run_name="b", target=["x"], phase=JobPhase.COMPLETED))
        assert store.count_active() == 1

    def test_search_by_run_name(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="pentest-web", target=["https://example.com"]))
        store.save(JobState(run_name="pentest-api", target=["https://api.example.com"]))
        store.save(JobState(run_name="other", target=["x"]))

        results = store.search("pentest")
        assert len(results) >= 2

        results = store.search("api")
        assert len(results) >= 1

    def test_search_by_target(self, tmp_path):
        store = JobStore(store_dir=tmp_path)
        store.save(JobState(run_name="run1", target=["https://example.com"]))
        store.save(JobState(run_name="run2", target=["https://other.com"]))

        results = store.search("example")
        assert len(results) >= 1

    def test_cleanup_old(self, tmp_path):
        import time
        store = JobStore(store_dir=tmp_path)
        old = JobState(run_name="old-job", target=["x"], phase=JobPhase.COMPLETED)
        old.start_time = 1000
        store.save(old)

        new = JobState(run_name="new-job", target=["x"], phase=JobPhase.COMPLETED)
        new.start_time = time.time()
        store.save(new)

        removed = store.cleanup_old(days=1)
        assert removed == 1
        assert store.get("old-job") is None
        assert store.get("new-job") is not None
