"""CPU-only public API abuse and immutable worker dependency tests."""

import base64
import hashlib
import json

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_modal_deployment_contract import load_mocked_modal_deployment
from test_notebook_workflow import FakeRequests, cell_source, context, execute_checked_in_cell

from tts_inference.contracts import ModelID
from tts_inference.gateway import create_gateway
from tts_inference.mock import mock_wav
from tts_inference.persistence import JobStore
from tts_inference.security import InProcessGuard


def test_production_disables_reference_voice_cloning_by_default(tmp_path, monkeypatch):
    token = "security-voice-client-very-long-random-token"
    other_token = "security-other-client-with-long-random-token"
    monkeypatch.delenv("TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON", raising=False)
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON",
        json.dumps({
            "client": hashlib.sha256(token.encode()).hexdigest(),
            "other": hashlib.sha256(other_token.encode()).hexdigest(),
        }),
    )
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_OWNER_TOKEN_SHA256",
        hashlib.sha256(b"separate-operator-credential-long-token").hexdigest(),
    )
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING", raising=False)
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'voice.sqlite3'}", Fernet.generate_key()
    )
    store.create_schema_for_tests()

    async def wake(_job_id):
        pass

    client = TestClient(create_gateway(
        job_store=store, guard=InProcessGuard(), wake_job=wake,
        production=True, enabled_models={ModelID.VOXCPM2},
    ))
    request = {
        "model": "voxcpm2", "input": "Hello",
        "reference_audio_base64": base64.b64encode(mock_wav()).decode(),
        "reference_consent": True,
    }
    response = client.post("/v1/audio/jobs", json=request, headers={
        "Authorization": "Bearer " + token,
    })
    assert response.status_code == 403
    assert store.safe_metrics()["queued"] == 0
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING", "1")
    # Global opt-in alone does not authorize cloning for any client.
    denied = client.post("/v1/audio/jobs", json=request, headers={
        "Authorization": "Bearer " + token,
    })
    assert denied.status_code == 403
    monkeypatch.setenv("TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON", '["client"]')
    allowed_client = TestClient(create_gateway(
        job_store=store, guard=InProcessGuard(), wake_job=wake,
        production=True, enabled_models={ModelID.VOXCPM2},
    ))
    other = allowed_client.post("/v1/audio/jobs", json=request, headers={
        "Authorization": "Bearer " + other_token,
    })
    assert other.status_code == 403
    accepted = allowed_client.post("/v1/audio/jobs", json=request, headers={
        "Authorization": "Bearer " + token,
    })
    assert accepted.status_code == 202


def test_gpu_image_requires_immutable_digest_and_selected_locked_runtime(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF", "lmsysorg/sglang-omni:dev")
    with pytest.raises(ValueError, match="immutable"):
        load_mocked_modal_deployment(monkeypatch)
    monkeypatch.delenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    with pytest.raises(RuntimeError, match="not selected"):
        module.MossTtsWorker().setup()
    with pytest.raises(RuntimeError, match="not selected"):
        module.VoxCPM2Worker().setup()

    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "voxcpm2")
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.voxcpm_package_version == "2.0.3"
    assert module.voxcpm2_worker_image.environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == "voxcpm2"

    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "higgs-tts-3")
    with pytest.raises(ValueError, match="license"):
        load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "1")
    with pytest.raises(ValueError, match="digest"):
        load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF",
                       "lmsysorg/sglang-omni@sha256:" + "a" * 64)
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.higgs_tts_3_worker_image.name.endswith("a" * 64)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED"] == "1"


@pytest.mark.parametrize("status,expected_state", [(422, "rejected"), (503, "uncertain")])
def test_notebook_distinguishes_rejected_from_unknown_post(
    monkeypatch, status, expected_state,
):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    fake = FakeRequests()
    variables = context(fake)
    execute_checked_in_cell(cell_source("prepare"), variables)

    def send_rejected(*_args, **_kwargs):
        response = requests.Response()
        response.status_code = status
        response.url = "http://127.0.0.1:8765/v1/audio/jobs"
        response.raise_for_status()
        return response

    fake.post = send_rejected
    submission = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submission, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == expected_state
    next_request = cell_source("prepare").replace(
        "REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2"
    )
    if expected_state == "rejected":
        execute_checked_in_cell(next_request, variables)
        assert variables["STABLE_REQUEST_ID"] != variables["_PREPARED_TTS_REQUEST"].get(
            "old_key", ""
        )
    else:
        with pytest.raises(ValueError, match="uncertain"):
            execute_checked_in_cell(next_request, variables)
