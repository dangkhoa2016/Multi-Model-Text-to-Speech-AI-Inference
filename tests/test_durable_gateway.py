"""HTTP-level security and durable queue tests without GPU/cloud services."""
import hashlib
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tts_inference.gateway import create_gateway
from tts_inference.mock import mock_wav
from tts_inference.persistence import JobStore
from tts_inference.wav import wave_metadata

TOKEN = "alice-long-high-entropy-test-token-12345"
BOB = "bob-long-high-entropy-test-token-123456"
OWNER = "owner-long-high-entropy-test-token-12345"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PAYLOAD = {"model": "voxcpm2", "input": "A private sentence"}


def make_app(tmp_path, monkeypatch=None):
    store = JobStore(f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}", Fernet.generate_key())
    store.create_schema_for_tests()
    wakeups = []

    async def wake(job_id):
        wakeups.append(job_id)

    if monkeypatch:
        monkeypatch.setenv("TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON", json.dumps({
            "alice": hashlib.sha256(TOKEN.encode()).hexdigest(),
            "bob": hashlib.sha256(BOB.encode()).hexdigest(),
        }))
        monkeypatch.setenv("TEXT_TO_SPEECH_OWNER_TOKEN_SHA256", hashlib.sha256(OWNER.encode()).hexdigest())
        app = create_gateway(job_store=store, wake_job=wake)
    else:
        app = create_gateway(job_store=store, wake_job=wake, client_keys={"alice": TOKEN, "bob": BOB})
    return TestClient(app), store, wakeups


def test_persistent_http_lifecycle_and_idempotency(tmp_path):
    client, store, wakeups = make_app(tmp_path)
    headers = {**HEADERS, "Idempotency-Key": "stable-key-00001"}
    response = client.post("/v1/audio/jobs", headers=headers, json=PAYLOAD)
    assert response.status_code == 202
    job = response.json()["job_id"]
    assert wakeups == [job]
    repeated = client.post("/v1/audio/jobs", headers=headers, json=PAYLOAD)
    assert repeated.status_code == 202
    assert repeated.json()["reused"] is True
    assert wakeups == [job]  # No duplicate GPU dispatch
    conflict = client.post("/v1/audio/jobs", headers=headers, json={**PAYLOAD, "input": "changed"})
    assert conflict.status_code == 409
    assert client.get(f"/v1/audio/jobs/{job}", headers=HEADERS).json()["status"] == "queued"
    assert client.get(f"/v1/audio/jobs/{job}/audio", headers=HEADERS).status_code == 409
    assert client.get(f"/v1/audio/jobs/{job}", headers={"Authorization": f"Bearer {BOB}"}).status_code == 404
    assert store.claim(job) is not None
    wav = mock_wav()  # Synthetic offline fixture, NOT model-generated speech.
    store.complete(job, wav, wave_metadata(wav), 8)
    assert client.get(f"/v1/audio/jobs/{job}", headers=HEADERS).json()["status"] == "completed"
    download = client.get(f"/v1/audio/jobs/{job}/audio", headers=HEADERS)
    assert download.status_code == 200 and download.content == wav
    assert download.headers["cache-control"] == "private, no-store"
    assert client.get(f"/v1/audio/jobs/{job}/audio", headers={"Authorization": f"Bearer {BOB}"}).status_code == 404


def test_hashed_token_and_owner_only_metrics(tmp_path, monkeypatch):
    client, _, _ = make_app(tmp_path, monkeypatch)
    assert client.get("/v1/models", headers=HEADERS).status_code == 200
    assert client.get("/v1/ops/metrics", headers=HEADERS).status_code == 404
    result = client.get("/v1/ops/metrics", headers={"Authorization": f"Bearer {OWNER}"})
    assert result.status_code == 200 and result.json()["scope"] == "persistent_database"
    assert client.get("/openapi.json").status_code == 404


def test_auth_lockout_and_valid_key_escape(tmp_path):
    client, _, _ = make_app(tmp_path)
    invalid = {"Authorization": "Bearer totally-bad-but-long-enough-12345"}
    assert [client.get("/v1/models", headers=invalid).status_code for _ in range(3)] == [401, 401, 429]
    assert client.get("/v1/models", headers=invalid).status_code == 429
    assert client.get("/v1/models", headers=HEADERS).status_code == 200
    assert client.get("/v1/models", headers=invalid).status_code == 401


def test_body_auth_before_validation_and_size_limit(tmp_path):
    client, _, _ = make_app(tmp_path)
    assert client.post("/v1/audio/jobs", content="{" + "A" * 100, headers={}).status_code == 401
    big = "a" * 8_500_002
    result = client.post("/v1/audio/jobs", headers=HEADERS, content=big)
    assert result.status_code == 413
    assert client.post("/v1/audio/jobs", headers=HEADERS, json={**PAYLOAD, "input": ""}).status_code == 422
    secret = "VERY_PRIVATE_PROMPT_TEXT_FOR_REDACTION"
    error = client.post("/v1/audio/jobs", headers=HEADERS, json={**PAYLOAD, "input": secret, "model_config": {"random_field": True}})
    assert error.status_code == 422
    assert secret not in error.text


def test_failed_wakeup_recovered_by_queue(tmp_path):
    store = JobStore(f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}", Fernet.generate_key())
    store.create_schema_for_tests()

    async def disconnected(_job_id):
        raise OSError("simulated CPU wake outage")

    client = TestClient(create_gateway(job_store=store, wake_job=disconnected, client_keys={"alice": TOKEN}))
    response = client.post("/v1/audio/jobs", headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 202
    assert response.json()["job_id"] in store.queued_ids()


def test_fail_closed_when_rate_store_unavailable(tmp_path):
    from tts_inference.security import InProcessGuard
    class BrokenGuard(InProcessGuard):
        async def preauth(self, peer):
            raise ConnectionError("redis unavailable")
    store = JobStore(f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}", Fernet.generate_key())
    store.create_schema_for_tests()
    client = TestClient(create_gateway(job_store=store, guard=BrokenGuard(), client_keys={"alice": TOKEN}))
    result = client.post("/v1/audio/jobs", headers=HEADERS, json=PAYLOAD)
    assert result.status_code == 503
    assert store.safe_metrics()["queued"] == 0


def test_duplicate_authorization_header_rejected(tmp_path):
    client, _, _ = make_app(tmp_path)
    result = client.get('/v1/models', headers=[
        ('Authorization', f'Bearer {TOKEN}'),
        ('Authorization', f'Bearer {BOB}'),
    ])
    assert result.status_code == 401
