"""CPU-only durable-state tests: SQLite substitutes for PostgreSQL locking semantics.

These verify state transitions but NOT PostgreSQL/Redis replica concurrency.
"""
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.mock import mock_wav
from tts_inference.persistence import (
    IdempotencyConflict,
    InvalidState,
    Job,
    JobStore,
    QueueFull,
    utcnow,
)
from tts_inference.wav import wave_metadata


def build_store(tmp_path, **kwargs):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(f"sqlite+pysqlite:///{db}", Fernet.generate_key(), **kwargs)
    store.create_schema_for_tests()
    return store


def speech(text="Private speech text", model="voxcpm2"):
    return prepare_request(SpeechRequest.model_validate({"model": model, "input": text}))


def test_queue_survives_store_reconstruction(tmp_path):
    key = Fernet.generate_key()
    uri = f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}"
    first = JobStore(uri, key)
    first.create_schema_for_tests()
    job_id, fresh = first.enqueue("alice", speech(), "stable-0001")
    second = JobStore(uri, key)
    assert fresh and second.inspect(job_id, "alice")["status"] == "queued"
    assert second.lookup_idempotency("alice", "stable-0001", speech()) == job_id
    assert second.inspect(job_id, "bob") is None


def test_idempotency_conflict_and_tenant_scope(tmp_path):
    s = build_store(tmp_path)
    job_id, new = s.enqueue("alice", speech(), "stable-0002")
    same_id, new2 = s.enqueue("alice", speech(), "stable-0002")
    assert (job_id, new) == (same_id, True)
    assert not new2
    assert s.enqueue("bob", speech(), "stable-0002")[0] != job_id
    with pytest.raises(IdempotencyConflict):
        s.enqueue("alice", speech("different text"), "stable-0002")
    with pytest.raises(IdempotencyConflict):
        s.lookup_idempotency("alice", "stable-0002", speech("different text"))


def test_queue_admission_and_per_model_running_limit(tmp_path):
    s = build_store(tmp_path, max_pending_per_owner=3, max_pending_per_model=3)
    ids = [s.enqueue("alice", speech(str(i)), f"key-000{i}")[0] for i in range(3)]
    with pytest.raises(QueueFull):
        s.enqueue("alice", speech("too many"))
    assert s.claim(ids[0])[1] == "voxcpm2"
    assert s.claim(ids[0]) is None  # Duplicate CPU wake cannot launch GPU twice.
    assert s.claim(ids[1]) is None  # One running job per model.
    wav = mock_wav()
    s.complete(ids[0], wav, wave_metadata(wav), 1.5)
    assert s.claim(ids[1]) is not None
    assert s.queued_ids(model="voxcpm2") == [ids[2]]


def test_encryption_of_raw_input_and_result(tmp_path):
    s = build_store(tmp_path)
    sample = "Unique secret voice sample text"
    j, _ = s.enqueue("alice", speech(sample))
    with s.sessions() as db:
        record = db.get(Job, j)
        assert sample.encode() not in record.encrypted_input
        assert record.encrypted_audio is None
    assert s.claim(j)
    wav = mock_wav()
    s.complete(j, wav, wave_metadata(wav), 15.7)
    with s.sessions() as db:
        record = db.get(Job, j)
        assert record.encrypted_input is None
        assert record.encrypted_audio != wav
        assert record.status == "completed"
    assert s.audio(j, "alice") == wav
    assert s.audio(j, "bob") is None
    assert s.inspect(j, "alice")["inference_ms"] == 16


def test_uncertain_outcome_not_queued(tmp_path):
    s = build_store(tmp_path)
    j, _ = s.enqueue("alice", speech())
    assert s.claim(j)
    s.uncertain(j)
    assert s.inspect(j, "alice")["status"] == "indeterminate"
    assert j not in s.queued_ids()
    assert s.claim(j) is None
    assert s.safe_metrics()["job_counts_by_state"]["indeterminate"] == 1


def test_heartbeat_stale_advisory_does_not_retry(tmp_path):
    s = build_store(tmp_path, stale_after_seconds=10)
    j, _ = s.enqueue("alice", speech())
    assert s.claim(j)
    with s.sessions.begin() as db:
        db.get(Job, j).heartbeat_at = utcnow() - timedelta(seconds=120)
    assert s.inspect(j, "alice")["status"] == "indeterminate"
    with s.sessions() as db:
        assert db.get(Job, j).status == "running"  # Read-only signal
    assert j not in s.queued_ids()
    wav = mock_wav()
    s.complete(j, wav, wave_metadata(wav), 40.0)  # Late GPU success allowed
    assert s.inspect(j, "alice")["status"] == "completed"


def test_operator_close_requires_confirmation(tmp_path):
    s = build_store(tmp_path)
    j, _ = s.enqueue("alice", speech())
    assert s.claim(j)
    with pytest.raises(ValueError):
        s.close_after_manual_gpu_confirmation(j, acknowledgement="unverified")
    assert s.inspect(j, "alice")["status"] == "running"
    s.close_after_manual_gpu_confirmation(j, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")
    assert s.inspect(j, "alice")["status"] == "failed"
    with pytest.raises(InvalidState):
        s.close_after_manual_gpu_confirmation(j, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")


def test_retention_preserves_indeterminate_and_running_model_slots(tmp_path):
    store = build_store(tmp_path, max_pending_per_owner=5, max_pending_per_model=5)
    unknown, _ = store.enqueue("alice", speech("uncertain"))
    queued, _ = store.enqueue("alice", speech("later"))
    completed, _ = store.enqueue("alice", speech("completed"), "completed-001")
    failed, _ = store.enqueue("alice", speech("failed"), "failed-0001")
    assert store.claim(unknown)
    store.uncertain(unknown)
    # A separate model is used so completed/failed states can be generated
    # without overlapping an uncertain invocation of the same model.
    with store.sessions.begin() as database:
        for job_id, status in ((completed, "completed"), (failed, "failed")):
            database.get(Job, job_id).status = status
        for job_id in (unknown, queued, completed, failed):
            database.get(Job, job_id).updated_at = utcnow() - timedelta(days=14)
    assert store.delete_expired_terminal(7) == 2
    assert store.inspect(unknown, "alice")["status"] == "indeterminate"
    assert store.inspect(queued, "alice")["status"] == "queued"
    assert store.inspect(completed, "alice") is None
    assert store.inspect(failed, "alice") is None
    assert store.claim(queued) is None
    store.close_after_manual_gpu_confirmation(unknown, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")
    assert store.claim(queued) is not None


def test_claim_rejects_disabled_model_without_occupying_slot(tmp_path):
    store = build_store(tmp_path)
    job_id, _ = store.enqueue("alice", speech(model="higgs-tts-3"))
    assert store.claim(job_id, enabled_models={"moss-tts-v1.5", "voxcpm2"}) is None
    assert store.inspect(job_id, "alice")["status"] == "queued"
    assert store.claim(job_id, enabled_models={"higgs-tts-3"}) is not None


def test_invalid_key_fails_no_enqueue(tmp_path):
    s = build_store(tmp_path)
    with pytest.raises(ValueError):
        s.enqueue("alice", speech(), "bad-key\nnew-line")
    assert s.safe_metrics()["queued"] == 0


def test_uncertain_gpu_blocks_same_model_until_operator_reconciles(tmp_path):
    s = build_store(tmp_path)
    first, _ = s.enqueue("alice", speech("first"))
    second, _ = s.enqueue("alice", speech("second"))
    assert s.claim(first)
    s.uncertain(first)
    assert s.claim(second) is None  # Must not launch another potentially overlapping GPU job.
    s.close_after_manual_gpu_confirmation(first, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")
    assert s.claim(second) is not None
