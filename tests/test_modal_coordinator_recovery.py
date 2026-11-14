"""CPU-only checks of the real scheduled sweep and corrupted GPU RPC results."""

import asyncio
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from test_modal_deployment_contract import load_mocked_modal_deployment

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.mock import mock_wav
from tts_inference.persistence import JobStore


def create_store(tmp_path):
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'sweep.sqlite3'}",
        Fernet.generate_key(),
        max_pending_per_owner=60,
        max_pending_per_model=30,
    )
    store.create_schema_for_tests()
    return store


def payload(text, model):
    return prepare_request(SpeechRequest.model_validate({
        "model": model, "input": text,
    }))


def test_actual_scheduled_sweep_wakes_unblocked_model(monkeypatch, tmp_path):
    store = create_store(tmp_path)
    for model in ("moss-tts-v1.5", "higgs-tts-3"):
        uncertain, _ = store.enqueue(model, payload("uncertain", model))
        assert store.claim(uncertain) is not None
        store.uncertain(uncertain)
        for index in range(19):
            store.enqueue(model, payload(f"blocked-{index}", model))
    eligible, _ = store.enqueue("new-client", payload("ready", "voxcpm2"))
    assert eligible not in store.queued_ids(30)

    monkeypatch.setenv("TEXT_TO_SPEECH_VOXCPM2_ENABLED", "1")
    modal_module = load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setattr(modal_module, "create_job_store", lambda: store)
    triggered = []

    async def fake_spawn(job_id):
        triggered.append(job_id)

    modal_module.dispatch_text_to_speech_job.spawn = SimpleNamespace(
        aio=fake_spawn,
    )
    asyncio.run(modal_module.resume_queued_text_to_speech_jobs())
    assert triggered == [eligible]


def test_coordinator_does_not_complete_truncated_gpu_output(monkeypatch, tmp_path):
    store = create_store(tmp_path)
    job_id, _ = store.enqueue("owner", payload("say hello", "voxcpm2"))
    monkeypatch.setenv("TEXT_TO_SPEECH_VOXCPM2_ENABLED", "1")
    modal_module = load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setattr(modal_module, "create_job_store", lambda: store)

    async def simulated_gpu_result(_job):
        return {
            "audio": mock_wav()[:44],
            "metadata": {"duration_seconds": 0.5},
            "inference_ms": 1,
        }

    class FakeVoxWorker:
        generate = SimpleNamespace(
            remote=SimpleNamespace(aio=simulated_gpu_result),
        )

    monkeypatch.setattr(modal_module, "VoxCPM2Worker", FakeVoxWorker)
    asyncio.run(modal_module.dispatch_text_to_speech_job(job_id))
    assert store.inspect(job_id, "owner")["status"] == "indeterminate"
    with pytest.raises(TimeoutError):
        store.audio(job_id, "owner")

def test_unconfigured_voxcpm_does_not_claim_or_call_gpu(monkeypatch, tmp_path):
    """A missing audited runtime cannot reserve a slot or allocate a worker."""
    store = create_store(tmp_path)
    job_id, _ = store.enqueue("owner", payload("safe CPU rejection", "voxcpm2"))
    monkeypatch.setenv("TEXT_TO_SPEECH_VOXCPM2_ENABLED", "0")
    modal_module = load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setattr(modal_module, "create_job_store", lambda: store)

    class ForbiddenWorker:
        def __init__(self):
            raise AssertionError("Unconfigured model must never allocate a GPU worker")

    monkeypatch.setattr(modal_module, "VoxCPM2Worker", ForbiddenWorker)
    asyncio.run(modal_module.dispatch_text_to_speech_job(job_id))
    assert store.inspect(job_id, "owner")["status"] == "queued"
