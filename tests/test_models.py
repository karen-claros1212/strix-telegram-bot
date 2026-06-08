"""Test models and state management."""

from __future__ import annotations

from strix_telegram_bot.models import (
    JobState,
    JobPhase,
    ScanMode,
    TargetType,
    ApprovalRequest,
)


def test_job_state_defaults():
    job = JobState(run_name="test-run", target=["https://example.com"])
    assert job.run_name == "test-run"
    assert job.phase == JobPhase.CREATED
    assert job.mode == ScanMode.DEEP
    assert job.is_active is False
    assert job.is_terminal is False


def test_job_state_active():
    job = JobState(run_name="test", target=["x"], phase=JobPhase.SCANNING)
    assert job.is_active is True
    assert job.is_terminal is False


def test_job_state_terminal():
    for phase in (JobPhase.COMPLETED, JobPhase.FAILED, JobPhase.STOPPED):
        job = JobState(run_name="t", target=["x"], phase=phase)
        assert job.is_terminal is True
        assert job.is_active is False


def test_job_serialization():
    job = JobState(
        run_name="test", target=["a"], mode=ScanMode.QUICK,
        phase=JobPhase.COMPLETED, instruction="test",
    )
    d = job.to_dict()
    assert d["run_name"] == "test"
    assert d["mode"] == "quick"

    restored = JobState.from_dict(d)
    assert restored.run_name == job.run_name
    assert restored.mode == job.mode


def test_scan_mode_values():
    assert ScanMode.QUICK.value == "quick"
    assert ScanMode.STANDARD.value == "standard"
    assert ScanMode.DEEP.value == "deep"


def test_target_type_values():
    assert TargetType.URL.value == "url"
    assert TargetType.MULTI.value == "multi"


def test_approval_request():
    req = ApprovalRequest(
        job_run_name="test", target=["x"],
        mode=ScanMode.DEEP, reason="deep scan",
        chat_id=123, message_id=456,
    )
    assert req.resolved is False
    assert req.job_run_name == "test"
