"""Read-only operations snapshot and optional Modal schedule, CPU-only."""
import json
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from test_modal_deployment_contract import load_mocked_modal_deployment

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.operations import collect_operations_snapshot
from tts_inference.persistence import Job, JobStore, utcnow


def store_with_schema(tmp_path, **kwargs):
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'operations.sqlite3'}",
        Fernet.generate_key(), **kwargs,
    )
    store.create_schema_for_tests()
    return store


def speech(private_text):
    return prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": private_text,
    }))


def test_snapshot_reports_stale_work_without_mutation_or_private_data(tmp_path):
    store = store_with_schema(tmp_path)
    job_id, _ = store.enqueue("private-owner", speech("PRIVATE_INPUT_TEXT"))
    with store.sessions.begin() as session:
        session.get(Job, job_id).created_at = utcnow() - timedelta(hours=1)
    snapshot = collect_operations_snapshot(store)
    assert snapshot["status"] == "attention"
    assert snapshot["delayed_queued_count"] == 1
    assert snapshot["job_counts_by_state"]["queued"] == 1
    text = json.dumps(snapshot)
    assert "private-owner" not in text and "PRIVATE_INPUT_TEXT" not in text
    assert job_id not in text
    assert store.inspect(job_id, "private-owner")["status"] == "queued"


def test_snapshot_includes_uncertain_work_and_estimated_budget(tmp_path):
    store = store_with_schema(
        tmp_path, estimated_gpu_micro_usd_per_job=1250000,
        maximum_estimated_gpu_micro_usd_per_day=5000000,
    )
    job_id, _ = store.enqueue("alice", speech("private"))
    assert store.claim(job_id)
    store.uncertain(job_id)
    snapshot = collect_operations_snapshot(store)
    assert snapshot["job_counts_by_state"]["indeterminate"] == 1
    assert snapshot["estimated_gpu_usd_reserved_today"] == "1.250000"
    assert snapshot["warnings"] == ["indeterminate"]


@pytest.mark.parametrize("invalid", [0, -1, True, "10"])
def test_invalid_snapshot_thresholds_are_rejected(tmp_path, invalid):
    store = store_with_schema(tmp_path)
    with pytest.raises(ValueError):
        collect_operations_snapshot(store, queued_age_seconds=invalid)


def test_modal_monitor_is_off_by_default_and_opt_in_only(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    declarations = [
        kw for name, kw in module.app.declarations
        if name == "function" and kw.get("schedule") is not None
    ]
    assert len(declarations) == 1  # The existing queued-job CPU sweep.
    assert callable(module.text_to_speech_operations_safety_monitor)
    monkeypatch.setenv("TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE", "hourly")
    scheduled = load_mocked_modal_deployment(monkeypatch)
    assert scheduled._operations_monitor_schedule() == {"minutes": 60}
    assert all("gpu" not in options for kind, options in scheduled.app.declarations if kind == "function")


def test_invalid_monitor_schedule_rejected_before_deploy(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE", "every-minute")
    with pytest.raises(ValueError, match="schedule"):
        load_mocked_modal_deployment(monkeypatch)
