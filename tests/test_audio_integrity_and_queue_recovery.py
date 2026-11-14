"""CPU regressions: fair queue sweep, WAV integrity and protected digests."""

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.mock import mock_wav
from tts_inference.persistence import Job, JobStore, _fingerprint_and_bytes
from tts_inference.wav import wave_metadata


def store_for(tmp_path, *, key=None, **options):
    encryption_key = key or Fernet.generate_key()
    database = f"sqlite+pysqlite:///{tmp_path / 'round2.sqlite3'}"
    store = JobStore(database, encryption_key, **options)
    store.create_schema_for_tests()
    return store, encryption_key, database


def request(text, model="voxcpm2"):
    return prepare_request(SpeechRequest.model_validate({"model": model, "input": text}))


def test_recovery_does_not_starve_enabled_model_behind_blocked_backlog(tmp_path):
    store, _, _ = store_for(
        tmp_path, max_pending_per_owner=60, max_pending_per_model=30
    )
    for model in ("moss-tts-v1.5", "higgs-tts-3"):
        unknown, _ = store.enqueue(model, request("unknown", model))
        assert store.claim(unknown)
        store.uncertain(unknown)
        for index in range(19):
            store.enqueue(model, request(f"backlog-{index}", model))
    ready, _ = store.enqueue("new-client", request("ready"))
    assert ready not in store.queued_ids(30)
    for _ in range(3):
        assert store.queued_ids_with_available_model_slots(
            {"moss-tts-v1.5", "higgs-tts-3", "voxcpm2"}
        ) == [ready]
    assert store.queued_ids_with_available_model_slots(
        {"moss-tts-v1.5", "higgs-tts-3"}
    ) == []
    assert store.claim(ready) is not None


@pytest.mark.parametrize("audio", [
    mock_wav()[:44],
    mock_wav()[:-100],
    b"",
])
def test_wav_header_without_full_pcm_is_rejected(audio):
    with pytest.raises(ValueError):
        wave_metadata(audio)


def test_corrupted_reference_and_completed_output_fail_closed(tmp_path):
    truncated_wav = mock_wav()[:44]
    with pytest.raises(ValueError):
        SpeechRequest.model_validate({
            "model": "voxcpm2",
            "input": "Hello",
            "reference_consent": True,
            "reference_audio_base64": base64.b64encode(truncated_wav).decode("ascii"),
        }).reference_bytes()
    store, _, _ = store_for(tmp_path)
    job, _ = store.enqueue("owner", request("test output"))
    assert store.claim(job)
    with pytest.raises(ValueError):
        store.complete(job, truncated_wav, {}, 20)
    assert store.inspect(job, "owner")["status"] == "running"
    assert wave_metadata(mock_wav())["duration_seconds"] == 0.5


def test_fingerprints_hide_plain_sha_and_scope_owners(tmp_path):
    store, key, database = store_for(tmp_path)
    payload = request("Guessable private input")
    legacy_digest, _ = _fingerprint_and_bytes(payload)
    first, _ = store.enqueue("alice", payload, "idempotent-alice-1")
    second, _ = store.enqueue("bob", payload, "idempotent-bob-0001")
    with store.sessions() as session:
        alice = session.get(Job, first)
        bob = session.get(Job, second)
        assert alice.payload_fingerprint_version == bob.payload_fingerprint_version == 2
        assert alice.payload_fingerprint != legacy_digest
        assert bob.payload_fingerprint != legacy_digest
        assert bob.payload_fingerprint != alice.payload_fingerprint
    restarted = JobStore(database, key)
    assert restarted.lookup_idempotency("alice", "idempotent-alice-1", payload) == first


def test_rekey_legacy_rows_including_completed_and_restarts(tmp_path):
    store, key, database = store_for(tmp_path)
    payload = request("Predictable prompt")
    legacy_hash, _ = _fingerprint_and_bytes(payload)
    current, _ = store.enqueue("alice", payload, "legacy-key-00001")
    with store.sessions.begin() as session:
        job = session.get(Job, current)
        job.payload_sha256 = legacy_hash
        job.payload_fingerprint = None
        job.payload_fingerprint_version = 1
        job.status = "completed"
        job.encrypted_input = None
    with pytest.raises(RuntimeError, match="legacy fingerprint rekey"):
        store.lookup_idempotency("alice", "legacy-key-00001", payload)
    assert store.migrate_legacy_payload_fingerprints() == 1
    assert store.migrate_legacy_payload_fingerprints() == 0
    restarted = JobStore(database, key)
    assert restarted.lookup_idempotency("alice", "legacy-key-00001", payload) == current
    with restarted.sessions() as session:
        job = session.get(Job, current)
        assert job.payload_fingerprint != hashlib.sha256(
            _fingerprint_and_bytes(payload)[1]
        ).hexdigest()
        assert job.payload_fingerprint_version == 2
