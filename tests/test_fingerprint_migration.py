"""Alembic upgrade regression for legacy unkeyed fingerprint rows."""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.persistence import Job, JobStore, _fingerprint_and_bytes


def test_legacy_schema_upgrade_rekeys_terminal_records_without_plaintext(
    tmp_path, monkeypatch,
):
    database = tmp_path / "historical.sqlite3"
    database_url = f"sqlite+pysqlite:///{database}"
    monkeypatch.setenv("TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL", database_url)
    repository_root = Path(__file__).resolve().parents[1]
    alembic_configuration = Config(str(repository_root / "alembic.ini"))
    alembic_configuration.set_main_option(
        "script_location", str(repository_root / "migrations"),
    )
    command.upgrade(alembic_configuration, "0001")

    request = prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": "common text",
    }))
    historical_sha, _ = _fingerprint_and_bytes(request)
    legacy_job_id = uuid.uuid4().hex
    instant = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO tts_jobs (
                id, owner, model, idempotency_key, payload_sha256,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_job_id, "historical-client", "voxcpm2",
                "legacy-request-0001", historical_sha,
                "completed", instant, instant,
            ),
        )

    command.upgrade(alembic_configuration, "head")
    key = Fernet.generate_key()
    store = JobStore(database_url, key)
    assert store.migrate_legacy_payload_fingerprints() == 1
    assert store.migrate_legacy_payload_fingerprints() == 0
    restarted = JobStore(database_url, key)
    assert restarted.lookup_idempotency(
        "historical-client", "legacy-request-0001", request,
    ) == legacy_job_id
    with restarted.sessions() as session:
        record = session.get(Job, legacy_job_id)
        assert record.encrypted_input is None
        assert record.payload_fingerprint != historical_sha
        assert record.payload_fingerprint_version == 2
