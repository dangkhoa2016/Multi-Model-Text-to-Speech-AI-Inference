"""Review regressions for notebook rejection semantics and migration/index drift."""

from pathlib import Path

import pytest
import requests
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from test_notebook_workflow import (
    FakeRequests,
    cell_source,
    context,
    execute_checked_in_cell,
)


def test_fresh_migration_matches_orm_and_uses_index(monkeypatch, tmp_path):
    path = tmp_path / "fresh-audit-index.sqlite3"
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL", f"sqlite+pysqlite:///{path}"
    )
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    indexes = {item["name"] for item in inspect(engine).get_indexes("tts_audit_events")}
    assert "ix_tts_audit_job_owner_id" in indexes
    with engine.begin() as connection:
        query = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT category, created_at FROM tts_audit_events "
            "WHERE job_id = ? AND owner = ? AND category IN (?, ?) "
            "ORDER BY id LIMIT 20",
            ("example-job", "example-owner", "job_accepted", "job_completed"),
        ).fetchall()
    assert any("ix_tts_audit_job_owner_id" in str(row) for row in query)


@pytest.mark.parametrize("status_code", [403, 422])
def test_notebook_rejected_http_allows_new_generation(monkeypatch, status_code):
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    fake = FakeRequests(submission_status=status_code)
    variables = context(fake)
    execute_checked_in_cell(cell_source("prepare"), variables)
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submit, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "rejected"
    old_key = variables["_PREPARED_TTS_REQUEST"]["key"]
    revised = cell_source("prepare").replace(
        "REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2", 1
    ).replace(
        'TEXT_TO_SYNTHESIZE = "Xin chào!',
        'TEXT_TO_SYNTHESIZE = "Đã sửa! Xin chào!',
        1,
    )
    execute_checked_in_cell(revised, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["key"] != old_key
    fake.submission_status = 200
    execute_checked_in_cell(submit, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "accepted"
    assert len(fake.posts) == 2


@pytest.mark.parametrize("status_code", [500, 503])
def test_notebook_uncertain_http_does_not_allow_new_generation(monkeypatch, status_code):
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    fake = FakeRequests(submission_status=status_code)
    variables = context(fake)
    execute_checked_in_cell(cell_source("prepare"), variables)
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submit, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "uncertain"
    revised = cell_source("prepare").replace("REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2", 1)
    with pytest.raises(ValueError, match="Prior POST outcome uncertain"):
        execute_checked_in_cell(revised, variables)


def test_production_policy_403_then_notebook_revises_without_duplicate_job(
    monkeypatch, tmp_path,
):
    """End-to-end HTTP 403 before enqueue; new generation reaches a CPU queue."""
    import base64

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from test_notebook_workflow import FakeResponse

    from tts_inference.contracts import ModelID
    from tts_inference.gateway import create_gateway
    from tts_inference.mock import mock_wav
    from tts_inference.persistence import JobStore
    from tts_inference.security import InProcessGuard

    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'policy.sqlite3'}",
        Fernet.generate_key(),
    )
    store.create_schema_for_tests()
    wakeups = []

    async def wake(job_id):
        wakeups.append(job_id)

    import hashlib
    import json

    bearer_token = "cpu-review-test-token-not-for-production-123"
    monkeypatch.setenv("TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON", json.dumps({
        "owner": hashlib.sha256(bearer_token.encode()).hexdigest(),
    }))
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_OWNER_TOKEN_SHA256",
        hashlib.sha256(b"another-disposable-operator-test-token-123").hexdigest(),
    )
    client = TestClient(create_gateway(
        job_store=store,
        wake_job=wake,
        guard=InProcessGuard(),
        production=True,
        enabled_models={ModelID("voxcpm2")},
    ))
    fake = FakeRequests()
    variables = context(fake)
    variables["AUTHENTICATED_REQUEST_HEADERS"] = {
        "Authorization": f"Bearer {bearer_token}"
    }
    variables["AUTHORIZED_WAV"] = base64.b64encode(mock_wav()).decode()

    def post_to_real_gateway(url, *, headers, json, timeout):
        fake.posts.append((url, headers, json, timeout))
        response = client.post("/v1/audio/jobs", headers=headers, json=json)
        return FakeResponse(status_code=response.status_code, data=response.json())

    fake.post = post_to_real_gateway
    with_reference = cell_source("prepare").replace(
        "request_fingerprint = hashlib.sha256(",
        'request_payload["reference_audio_base64"] = AUTHORIZED_WAV\n'
        'request_payload["reference_consent"] = True\n'
        "request_fingerprint = hashlib.sha256(",
        1,
    )
    execute_checked_in_cell(with_reference, variables)
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submit, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "rejected"
    assert store.safe_metrics()["queued"] == 0
    assert wakeups == []
    new_generation = cell_source("prepare").replace(
        "REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2", 1
    )
    execute_checked_in_cell(new_generation, variables)
    execute_checked_in_cell(submit, variables)
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "accepted"
    assert len(wakeups) == 1
    assert store.safe_metrics()["queued"] == 1


@pytest.mark.parametrize("filename", ["DEPLOYMENT.md", "DEPLOYMENT.vi.md"])
def test_modal_deployment_docs_are_cpu_first_and_environment_scoped(filename):
    """A new operator can follow the documented read-only acceptance path."""
    guide = (Path(__file__).resolve().parents[1] / "docs" / filename).read_text()
    for required in [
        'export MODAL_ENVIRONMENT="main"',
        'modal config show --redact',
        'modal environment list',
        'modal secret list --env "$MODAL_ENVIRONMENT"',
        'modal volume list --env "$MODAL_ENVIRONMENT"',
        'modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access',
        'modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_tts_model',
        'modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py',
        'modal app info text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"',
        '"$TTS_BASE_URL/v1/health"',
        '"$TTS_BASE_URL/v1/models"',
        'read -r -s -p "Client Bearer token: "',
        'TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF',
        'TEXT_TO_SPEECH_VOXCPM_PACKAGE_VERSION',
        'HIGGS_TTS_3_LICENSE_CONFIRMED',
        'modal app rollover text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"',
        'modal app history text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"',
        'alembic upgrade head',
        '0006',
        '--confirm-stopped',
    ]:
        assert required in guide, (filename, required)
    assert guide.count("curl --silent --show-error") == 4
    assert "POST /v1/audio/jobs" not in guide.split("## 10.")[1].split("## 11.")[0]


def test_modal_deployment_docs_include_parallel_steps():
    root = Path(__file__).resolve().parents[1] / "docs"
    en = (root / "DEPLOYMENT.md").read_text()
    vi = (root / "DEPLOYMENT.vi.md").read_text()
    for number in (8, 9, 10, 11):
        assert f"## {number}." in en and f"## {number}." in vi
    assert "First-install acceptance checklist" in en
    assert "Checklist nghiệm thu khi cài đặt từ đầu" in vi
