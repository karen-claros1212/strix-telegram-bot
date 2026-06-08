"""Test Persistent State Manager."""

from __future__ import annotations

from strix_telegram_bot.state.state_manager import StateManager


class TestStateManager:
    def test_get_set(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        assert sm.get("nonexistent") is None
        assert sm.get("nonexistent", "default") == "default"

        sm.set("key1", "value1")
        assert sm.get("key1") == "value1"

    def test_update(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        sm.update({"a": 1, "b": 2})
        assert sm.get("a") == 1
        assert sm.get("b") == 2

    def test_delete(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        sm.set("key", "val")
        assert sm.delete("key") is True
        assert sm.get("key") is None

    def test_delete_nonexistent(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        assert sm.delete("nothing") is False

    def test_active_job(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        assert sm.get_active_job_id() is None

        sm.set_active_job_id("job-123")
        assert sm.get_active_job_id() == "job-123"

        sm.clear_active_job()
        assert sm.get_active_job_id() is None

    def test_event_checkpoint(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        assert sm.get_event_checkpoint("run-x") == 0

        sm.set_event_checkpoint("run-x", 500)
        assert sm.get_event_checkpoint("run-x") == 500

    def test_persistence_across_instances(self, tmp_path):
        sm1 = StateManager(state_dir=tmp_path)
        sm1.set("persist_key", "persist_val")

        sm2 = StateManager(state_dir=tmp_path)
        assert sm2.get("persist_key") == "persist_val"

    def test_to_dict(self, tmp_path):
        sm = StateManager(state_dir=tmp_path)
        d = sm.to_dict()
        assert "active_job_id" in d
        assert "event_checkpoints" in d
        assert "uptime_start" in d
