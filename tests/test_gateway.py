from fastapi.testclient import TestClient

from tts_inference.gateway import create_gateway
from tts_inference.mock import MockDispatcher

TOKEN = "test-only-long-secret-token-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_authenticated_mock_job_and_audio():
    from tts_inference.contracts import ModelID

    app = create_gateway(MockDispatcher(), client_keys={"alice": TOKEN, "bob": "another-32-character-test-secret-1234"}, enabled_models=set(ModelID))
    client = TestClient(app)
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers=AUTH).status_code == 200
    result = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "higgs-tts-3", "input": "Hi", "model_config": {"emotion": "relief"},
    })
    assert result.status_code == 202
    job_id = result.json()["job_id"]
    assert result.headers["location"].endswith(job_id)
    status = client.get(f"/v1/audio/jobs/{job_id}", headers=AUTH)
    assert status.json()["status"] == "completed"
    assert status.json()["is_mock_result"] is True
    assert status.json()["audio_is_speech"] is False
    wav = client.get(f"/v1/audio/jobs/{job_id}/audio", headers=AUTH)
    assert wav.status_code == 200
    assert wav.content.startswith(b"RIFF")
    assert wav.headers["cache-control"] == "private, no-store"
    assert client.get(f"/v1/audio/jobs/{job_id}", headers={"Authorization": "Bearer another-32-character-test-secret-1234"}).status_code == 404


def test_invalid_option_pre_dispatch():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={"a": TOKEN}))
    response = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "moss-tts-v1.5", "input": "Hi", "model_config": {"gender": "female"},
    })
    assert response.status_code == 422


def test_no_reference_consent():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={"a": TOKEN}))
    response = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "voxcpm2", "input": "Hi", "reference_audio_base64": "AAAA",
    })
    assert response.status_code == 422


def test_throttle():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={"a": TOKEN}))
    for _ in range(12):
        assert client.post("/v1/audio/jobs", headers=AUTH, json={"model": "voxcpm2", "input": "Hi"}).status_code == 202
    response = client.post("/v1/audio/jobs", headers=AUTH, json={"model": "voxcpm2", "input": "Hi"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"


def test_cross_field_validation_error_is_serializable():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={"a": TOKEN}))
    response = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "moss-tts-v1.5", "input": "Hi",
        "model_config": {"max_new_tokens": 64, "duration_tokens": 200},
    })
    assert response.status_code == 422
    assert "duration_tokens" in response.text


def test_vox_denoiser_rejected_on_cpu():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={"a": TOKEN}))
    response = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "voxcpm2", "input": "Hi", "model_config": {"denoise": True},
    })
    assert response.status_code == 422


def test_higgs_catalog_visibility_does_not_enable_unlicensed_inference():
    from tts_inference.contracts import ModelID

    client = TestClient(create_gateway(
        MockDispatcher(), client_keys={"alice": TOKEN},
        enabled_models={ModelID.MOSS_TTS, ModelID.VOXCPM2},
    ))
    models = client.get("/v1/models", headers=AUTH).json()["data"]
    assert next(item for item in models if item["id"] == "higgs-tts-3")["enabled"] is False
    assert client.get("/v1/models/higgs-tts-3", headers=AUTH).json()["enabled"] is False
    denied = client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "higgs-tts-3", "input": "This must not start a worker"
    })
    assert denied.status_code == 503
    assert "disabled by deployment operator" in denied.json()["detail"]
    assert client.post("/v1/audio/jobs", headers=AUTH, json={
        "model": "voxcpm2", "input": "This model is allowed"
    }).status_code == 202
