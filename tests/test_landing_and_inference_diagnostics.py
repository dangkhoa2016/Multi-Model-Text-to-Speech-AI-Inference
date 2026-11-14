"""Public landing, owner-scoped telemetry, migrations and CPU-only worker regression."""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_modal_deployment_contract import load_mocked_modal_deployment

from tts_inference.gateway import create_gateway
from tts_inference.mock import MockDispatcher, mock_wav
from tts_inference.persistence import JobStore


def durable_store(tmp_path):
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'speech.sqlite3'}", Fernet.generate_key()
    )
    store.create_schema_for_tests()
    return store


def test_static_landing_english_and_vietnamese_does_not_open_gpu():
    app = create_gateway(
        MockDispatcher(),
        client_keys={"client": "very-long-client-test-token-for-authentication"},
    )
    client = TestClient(app)
    english = client.get("/")
    assert english.status_code == 200
    assert english.headers["content-type"].startswith("text/html")
    assert english.headers["cache-control"] == "no-store"
    assert english.headers["x-frame-options"] == "DENY"
    assert english.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in english.headers["content-security-policy"]
    assert 'lang="en"' in english.text
    assert 'href="/?lang=vi"' in english.text
    assert english.text.count('<article class="card">') == 3
    assert all(name in english.text for name in (
        "moss-tts-v1.5", "higgs-tts-3", "voxcpm2",
    ))
    assert "GET /v1/health" in english.text
    assert "POST /v1/audio/jobs" in english.text
    assert "<script" not in english.text.lower()
    assert "GPU Online" not in english.text
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/health").status_code == 200
    vietnamese = client.get("/?lang=vi")
    assert vietnamese.status_code == 200
    assert 'lang="vi"' in vietnamese.text
    assert "Ba model giọng nói" in vietnamese.text
    assert 'href="/?lang=en"' in vietnamese.text
    assert client.get("/?lang=other").text == english.text


def test_durable_owner_only_metrics_and_redacted_event_timeline(tmp_path):
    store = durable_store(tmp_path)
    text = "Confidential speech content, never returned"
    job_id, created = store.enqueue(
        "alice", {"model": "voxcpm2", "input": text}, "telemetry-client-key-01",
    )
    assert created
    queued = store.inspect(job_id, "alice")
    assert queued["status"] == "queued"
    assert queued["usage"]["input_characters"] == len(text)
    assert queued["usage"]["input_tokens"] is None
    assert queued["usage"]["output_tokens"] is None
    assert queued["timing"]["queue_wait_ms"] is None
    assert queued["timing"]["model_load_ms"] is None
    assert [x["event"] for x in queued["diagnostics"]["events"]] == ["job_accepted"]
    assert queued["diagnostics"]["request_id"] == job_id
    assert text not in json.dumps(queued)
    assert store.inspect(job_id, "bob") is None

    assert store.claim(job_id)
    running = store.inspect(job_id, "alice")
    assert running["timing"]["queue_wait_ms"] >= 0
    assert [x["event"] for x in running["diagnostics"]["events"]] == [
        "job_accepted", "job_claimed",
    ]

    store.complete(
        job_id, mock_wav(), {}, 90.1, worker_wall_ms=115.3, audio_validation_ms=2.2,
    )
    done = store.inspect(job_id, "alice")
    assert done["status"] == "completed"
    assert done["inference_ms"] == 90
    assert done["timing"]["inference_ms"] == 90
    assert done["timing"]["worker_wall_ms"] == 115
    assert done["timing"]["audio_validation_ms"] == 2
    assert done["timing"]["total_ms"] >= 0
    assert done["usage"]["audio_duration_seconds"] == 0.5
    assert done["usage"]["realtime_factor"] == 0.18
    assert done["usage"]["input_tokens"] is None
    assert done["usage"]["output_tokens"] is None
    assert done["diagnostics"]["warnings"] == []
    assert [x["event"] for x in done["diagnostics"]["events"]] == [
        "job_accepted", "job_claimed", "job_completed",
    ]
    assert all(x["at"] is not None for x in done["diagnostics"]["events"])
    assert text not in json.dumps(done)
    assert store.inspect(job_id, "bob") is None


def test_durable_status_protects_unknown_errors_and_audit(tmp_path):
    store = durable_store(tmp_path)
    job_id, _ = store.enqueue("alice", {
        "model": "moss-tts-v1.5", "input": "private voice payload",
    })
    store.claim(job_id)
    store.uncertain(job_id, code="gpu_outcome_unknown")
    info = store.inspect(job_id, "alice")
    assert info["status"] == "indeterminate"
    assert info["diagnostics"]["error_code"] == "gpu_outcome_unknown"
    assert info["timing"]["inference_ms"] is None
    assert info["diagnostics"]["warnings"]
    assert "private voice payload" not in json.dumps(info)
    assert store.inspect(job_id, "another-owner") is None
    with store.sessions.begin() as session:
        from tts_inference.persistence import Job
        record = session.get(Job, job_id)
        record.error_code = "secret\nTraceback: leaked"
    info = store.inspect(job_id, "alice")
    assert info["diagnostics"]["error_code"] == "unspecified_error"


def test_local_cpu_mock_reports_no_real_gpu_tokens_or_inference():
    token = "very-long-client-test-token-for-authentication"
    app = create_gateway(MockDispatcher(), client_keys={"client": token})
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/v1/audio/jobs", headers=headers, json={
        "model": "voxcpm2", "input": "Hello from local mock",
    })
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    info = client.get(f"/v1/audio/jobs/{job_id}", headers=headers).json()
    assert info["is_mock_result"] is True
    assert info["audio_is_speech"] is False
    assert info["inference_ms"] is None
    assert info["usage"]["input_characters"] == len("Hello from local mock")
    assert info["usage"]["input_tokens"] is None
    assert info["timing"]["worker_wall_ms"] is None
    assert info["diagnostics"]["request_id"] == job_id
    assert info["diagnostics"]["events"][0]["event"] == "mock_completed"
    assert any("Synthetic" in x for x in info["diagnostics"]["warnings"])
    assert re.fullmatch(r"[0-9a-f]{32}", job_id)


def test_coordinator_populates_worker_wall_and_validation_metrics(tmp_path, monkeypatch):
    store = durable_store(tmp_path)
    job_id, _ = store.enqueue("alice", {"model": "voxcpm2", "input": "Synthetic voice"})
    monkeypatch.setenv("TEXT_TO_SPEECH_VOXCPM2_ENABLED", "1")
    module = load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setattr(module, "create_job_store", lambda: store)

    async def fake_gpu(_job):
        return {"audio": mock_wav(), "inference_ms": 30.0}

    class FakeWorker:
        generate = SimpleNamespace(remote=SimpleNamespace(aio=fake_gpu))

    monkeypatch.setattr(module, "VoxCPM2Worker", FakeWorker)
    asyncio.run(module.dispatch_text_to_speech_job(job_id))
    result = store.inspect(job_id, "alice")
    assert result["status"] == "completed"
    assert result["inference_ms"] == 30
    assert result["timing"]["worker_wall_ms"] is not None
    assert result["timing"]["audio_validation_ms"] is not None
    assert result["timing"]["worker_wall_ms"] >= 0
    assert result["timing"]["audio_validation_ms"] >= 0
    assert result["usage"]["input_tokens"] is None


def test_upgrade_from_0003_adds_nullable_telemetry_without_dropping_jobs(
    tmp_path, monkeypatch,
):
    database = tmp_path / "migration.sqlite3"
    url = f"sqlite+pysqlite:///{database}"
    monkeypatch.setenv("TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    configuration = Config(str(root / "alembic.ini"))
    configuration.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(configuration, "0003")
    store = JobStore(url, Fernet.generate_key())
    with store.sessions.begin() as session:
        from sqlalchemy import text
        session.execute(text(
            "INSERT INTO tts_jobs (id, owner, model, payload_sha256, "
            "payload_fingerprint, payload_fingerprint_version, status, created_at, updated_at) "
            "VALUES ('0123456789abcdef0123456789abcdef', 'alice', 'voxcpm2', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 2, "
            "'queued', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))
    command.upgrade(configuration, "head")
    record = store.inspect("0123456789abcdef0123456789abcdef", "alice")
    assert record["usage"]["input_characters"] is None
    assert record["timing"]["queue_wait_ms"] is None
    assert record["status"] == "queued"
