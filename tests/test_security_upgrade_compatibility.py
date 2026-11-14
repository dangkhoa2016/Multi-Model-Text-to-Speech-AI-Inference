"""Security regression tests: legacy migration compatibility and PCM alignment."""

import io
import sqlite3
import struct
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import DateTime, String, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.persistence import Job, JobStore, _fingerprint_and_bytes
from tts_inference.wav import wave_metadata


class PreviousBase(DeclarativeBase):
    pass


class PreviousRunnerJob(PreviousBase):
    __tablename__ = "tts_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(48))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def old_database(monkeypatch, path):
    monkeypatch.setenv("TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL",
                       f"sqlite+pysqlite:///{path}")
    configuration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    configuration.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "migrations")
    )
    command.upgrade(configuration, "0001")
    payload = prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": "A private phrase",
    }))
    digest, _ = _fingerprint_and_bytes(payload)
    identifier = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO tts_jobs (id,owner,model,idempotency_key,payload_sha256,"
            "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (identifier,"client","voxcpm2","legacy-key-0001",digest,"running",now,now),
        )
    return configuration, identifier, payload


def test_older_writer_can_complete_after_expand_migration(monkeypatch,tmp_path):
    path=tmp_path/"old.sqlite3"
    configuration,identifier,_=old_database(monkeypatch,path)
    command.upgrade(configuration,"head")
    sessions=sessionmaker(create_engine(f"sqlite+pysqlite:///{path}"))
    with sessions.begin() as session:
        job=session.get(PreviousRunnerJob,identifier)
        assert job.payload_sha256
        job.status="completed"
    with sessions() as session:
        assert session.get(PreviousRunnerJob,identifier).status=="completed"


def test_earlier_rename_migration_is_repaired(monkeypatch,tmp_path):
    path=tmp_path/"renamed.sqlite3"
    configuration,identifier,payload=old_database(monkeypatch,path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE tts_jobs RENAME COLUMN payload_sha256 TO payload_fingerprint")
        db.execute("ALTER TABLE tts_jobs ADD COLUMN payload_fingerprint_version "
                   "INTEGER NOT NULL DEFAULT 1")
    command.stamp(configuration,"0002")
    command.upgrade(configuration,"head")
    store=JobStore(f"sqlite+pysqlite:///{path}",Fernet.generate_key())
    assert store.migrate_legacy_payload_fingerprints(batch_size=1)==1
    assert store.lookup_idempotency("client","legacy-key-0001",payload)==identifier
    columns = {item["name"]: item for item in inspect(store.engine).get_columns("tts_jobs")}
    assert columns["payload_sha256"]["nullable"] is False
    assert columns["payload_fingerprint"]["nullable"] is True
    assert any(index["name"] == "ix_tts_audit_job_owner_id"
               for index in inspect(store.engine).get_indexes("tts_audit_events"))
    command.check(configuration)


def test_bounded_rekey_never_selects_encrypted_audio(tmp_path):
    store=JobStore(f"sqlite+pysqlite:///{tmp_path/'batch.sqlite3'}",Fernet.generate_key())
    store.create_schema_for_tests()
    for number in range(5):
        payload={"model":"voxcpm2","input":str(number)}
        identifier,_=store.enqueue("client",payload)
        with store.sessions.begin() as session:
            job=session.get(Job,identifier)
            job.payload_sha256=_fingerprint_and_bytes(payload)[0]
            job.payload_fingerprint=None
            job.payload_fingerprint_version=1
            job.encrypted_audio=b"ciphertext"*100000
    selects=[]
    def capture(_conn,_cursor,statement,_params,_context,_many):
        if statement.lstrip().lower().startswith("select"):
            selects.append(statement.lower())
    event.listen(store.engine,"before_cursor_execute",capture)
    try:
        assert store.migrate_legacy_payload_fingerprints(batch_size=2)==5
    finally:
        event.remove(store.engine,"before_cursor_execute",capture)
    queries=[sql for sql in selects if "from tts_jobs" in sql]
    assert len(queries)>=3
    assert all("encrypted_audio" not in sql and "encrypted_input" not in sql
               for sql in queries)


def test_riff_partial_pcm_frame_rejected():
    sink=io.BytesIO()
    with wave.open(sink,"wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00"*100)
    raw=bytearray(sink.getvalue())
    assert wave_metadata(bytes(raw))["duration_seconds"]>0
    position=raw.index(b"data")
    size=struct.unpack_from("<I",raw,position+4)[0]
    struct.pack_into("<I",raw,position+4,size+1)
    struct.pack_into("<I",raw,4,len(raw)-8+2)
    raw.extend(b"\x7f\x00")
    with pytest.raises(ValueError,match="aligned"):
        wave_metadata(bytes(raw))
