"""Tests for the per-run, persisted report-delivery state machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strix_telegram_bot.strix.delivery_state import (
    DeliveryRecord,
    DeliveryState,
    ReportDeliveryTracker,
)


@pytest.fixture
def tracker(tmp_path) -> ReportDeliveryTracker:
    return ReportDeliveryTracker(store_dir=tmp_path / ".bot-delivery")


class TestDeliveryStateEnum:
    def test_terminal_states(self):
        assert DeliveryState.DELIVERED.is_terminal
        assert DeliveryState.PERMANENT_FAILURE.is_terminal
        assert not DeliveryState.PENDING.is_terminal
        assert not DeliveryState.DELIVERING.is_terminal
        assert not DeliveryState.NOT_ELIGIBLE.is_terminal
        assert not DeliveryState.TRANSIENT_FAILURE.is_terminal

    def test_retryable_states(self):
        assert DeliveryState.PENDING.is_retryable
        assert DeliveryState.TRANSIENT_FAILURE.is_retryable
        assert not DeliveryState.DELIVERED.is_retryable
        assert not DeliveryState.PERMANENT_FAILURE.is_retryable
        assert not DeliveryState.NOT_ELIGIBLE.is_retryable


class TestDeliveryRecord:
    def test_roundtrip(self):
        rec = DeliveryRecord(
            run_name="scan-1",
            chat_id=42,
            state=DeliveryState.TRANSIENT_FAILURE,
            attempt_count=3,
            last_attempt=1234.5,
            delivered=False,
        )
        data = rec.to_dict()
        assert data["state"] == "transient_failure"
        restored = DeliveryRecord.from_dict(data)
        assert restored.run_name == "scan-1"
        assert restored.chat_id == 42
        assert restored.state == DeliveryState.TRANSIENT_FAILURE
        assert restored.attempt_count == 3
        assert restored.last_attempt == 1234.5

    def test_from_dict_unknown_state_defaults_not_eligible(self):
        rec = DeliveryRecord.from_dict({"run_name": "x", "state": "bogus"})
        assert rec.state == DeliveryState.NOT_ELIGIBLE

    def test_from_dict_missing_optional_fields(self):
        rec = DeliveryRecord.from_dict({"run_name": "x"})
        assert rec.chat_id == 0
        assert rec.state == DeliveryState.NOT_ELIGIBLE
        assert rec.attempt_count == 0
        assert rec.delivered is False


class TestTrackerTransitions:
    def test_initial_state_not_eligible(self, tracker):
        rec = tracker.get_or_create("scan-a", 1)
        assert rec.state == DeliveryState.NOT_ELIGIBLE

    def test_success_marks_delivered(self, tracker):
        rec = tracker.record_attempt("scan-a", "success", 1)
        assert rec.state == DeliveryState.DELIVERED
        assert rec.delivered is True
        assert rec.attempt_count == 1

    def test_transient_failure(self, tracker):
        rec = tracker.record_attempt("scan-a", "transient", 1)
        assert rec.state == DeliveryState.TRANSIENT_FAILURE
        assert rec.delivered is False
        assert rec.attempt_count == 1

    def test_permanent_failure(self, tracker):
        rec = tracker.record_attempt("scan-a", "permanent", 1)
        assert rec.state == DeliveryState.PERMANENT_FAILURE
        assert rec.delivered is False

    def test_attempt_count_increments(self, tracker):
        tracker.record_attempt("scan-a", "transient", 1)
        rec = tracker.record_attempt("scan-a", "transient", 1)
        assert rec.attempt_count == 2

    def test_set_state_delivered_sets_marker(self, tracker):
        rec = tracker.set_state("scan-a", DeliveryState.DELIVERED, 1)
        assert rec.delivered is True


class TestNoCrossRunFallback:
    def test_independent_records_per_run(self, tracker):
        a = tracker.get_or_create("scan-a", 1)
        b = tracker.get_or_create("scan-b", 2)
        tracker.record_attempt("scan-a", "success", 1)
        # Delivering scan-a must not affect scan-b
        assert a.state == DeliveryState.DELIVERED
        assert b.state == DeliveryState.NOT_ELIGIBLE

    def test_chat_id_update_does_not_leak(self, tracker):
        tracker.get_or_create("scan-a", 1)
        rec_b = tracker.get_or_create("scan-b", 2)
        assert rec_b.chat_id == 2
        assert tracker.get("scan-a").chat_id == 1


class TestPersistence:
    def test_state_survives_restart(self, tmp_path):
        store = tmp_path / ".bot-delivery"
        t1 = ReportDeliveryTracker(store_dir=store)
        t1.record_attempt("scan-a", "transient", 1)
        t1.record_attempt("scan-a", "success", 1)

        # Simulate restart: new tracker instance, same store dir
        t2 = ReportDeliveryTracker(store_dir=store)
        rec = t2.get("scan-a")
        assert rec is not None
        assert rec.state == DeliveryState.DELIVERED
        assert rec.delivered is True
        assert rec.attempt_count == 2

    def test_persisted_file_shape(self, tmp_path):
        store = tmp_path / ".bot-delivery"
        t = ReportDeliveryTracker(store_dir=store)
        t.record_attempt("scan-a", "permanent", 7)
        fpath = store / "scan-a.json"
        assert fpath.is_file()
        data = json.loads(fpath.read_text())
        assert data["run_name"] == "scan-a"
        assert data["chat_id"] == 7
        assert data["state"] == "permanent_failure"
        assert data["attempt_count"] == 1


class TestRecovery:
    def test_delivering_becomes_pending_on_restart(self, tmp_path):
        store = tmp_path / ".bot-delivery"
        t1 = ReportDeliveryTracker(store_dir=store)
        t1.set_state("scan-a", DeliveryState.DELIVERING, 1)

        t2 = ReportDeliveryTracker(store_dir=store)
        recovered = t2.recover()
        assert recovered == 1
        assert t2.get("scan-a").state == DeliveryState.PENDING

    def test_recover_noop_when_no_delivering(self, tracker):
        tracker.set_state("scan-a", DeliveryState.DELIVERED, 1)
        assert tracker.recover() == 0
        assert tracker.get("scan-a").state == DeliveryState.DELIVERED


class TestListNeedingDelivery:
    def test_includes_pending_transient_delivering(self, tracker):
        tracker.set_state("a", DeliveryState.PENDING, 1)
        tracker.record_attempt("b", "transient", 1)
        tracker.set_state("c", DeliveryState.DELIVERING, 1)
        tracker.set_state("d", DeliveryState.DELIVERED, 1)
        tracker.set_state("e", DeliveryState.PERMANENT_FAILURE, 1)
        tracker.get_or_create("f", 1)  # NOT_ELIGIBLE

        names = {r.run_name for r in tracker.list_needing_delivery()}
        assert names == {"a", "b", "c"}


class TestDelete:
    def test_delete_removes_record_and_file(self, tracker, tmp_path):
        tracker.record_attempt("scan-a", "success", 1)
        fpath = tmp_path / ".bot-delivery" / "scan-a.json"
        assert fpath.exists()
        assert tracker.delete("scan-a") is True
        assert tracker.get("scan-a") is None
        assert not fpath.exists()
        assert tracker.delete("scan-a") is False
