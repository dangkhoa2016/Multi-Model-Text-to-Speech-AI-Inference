"""Durable, encrypted job ledger. PostgreSQL is authoritative; SQLite is test-only.

The database contains encrypted prompts, reference WAVs and generated audio.
It does not implement a broker that guarantees exactly-once GPU execution:
uncertain running jobs are reported INDETERMINATE and never auto-retried.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class AdmissionLock(Base):
    __tablename__ = "admission_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class Job(Base):
    __tablename__ = "tts_jobs"
    __table_args__ = (
        UniqueConstraint("owner", "idempotency_key", name="uq_tts_owner_idempotency"),
        Index("ix_tts_job_state_model", "status", "model", "created_at"),
        Index("ix_tts_job_owner_state", "owner", "status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_input: Mapped[bytes | None] = mapped_column(LargeBinary)
    encrypted_audio: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    audio_metadata: Mapped[str | None] = mapped_column(Text)
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "tts_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(100))
    job_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IdempotencyConflict(Exception):
    pass


class QueueFull(Exception):
    pass


class InvalidState(Exception):
    pass


def _fingerprint_and_bytes(payload: dict) -> tuple[str, bytes]:
    safe = {**payload}
    reference = safe.get("reference_audio")
    if isinstance(reference, bytes):
        safe["reference_audio"] = base64.b64encode(reference).decode("ascii")
    packed = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(packed).hexdigest(), packed


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops TZ information; PostgreSQL timestamptz does not.
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class JobStore:
    """Synchronous SQLAlchemy operations, called via asyncio.to_thread in the API."""

    def __init__(self, database_url: str, encryption_key: str | bytes, *,
                 max_pending_per_owner: int = 20, max_pending_per_model: int = 20,
                 max_running_per_model: int = 1, stale_after_seconds: int = 180):
        if not database_url or not encryption_key:
            raise ValueError("Database URL and a persistent Fernet key are required")
        if database_url.startswith("sqlite") and ":memory:" in database_url:
            self.engine = create_engine(database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            self.engine = create_engine(database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.fernet = Fernet(encryption_key)
        self.max_pending_per_owner = max_pending_per_owner
        self.max_pending_per_model = max_pending_per_model
        self.max_running_per_model = max_running_per_model
        self.stale_after_seconds = stale_after_seconds
        for v in (max_pending_per_owner, max_pending_per_model, max_running_per_model, stale_after_seconds):
            if v < 1:
                raise ValueError("Storage limits must be positive")

    def create_schema_for_tests(self):
        """For offline SQLite tests ONLY. Production uses Alembic migration."""
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as session:
            if session.get(AdmissionLock, 1) is None:
                session.add(AdmissionLock(id=1))

    def check_ready(self) -> bool:
        with self.sessions() as s:
            return s.get(AdmissionLock, 1) is not None

    def _audit(self, s, category: str, owner: str | None = None, job_id: str | None = None):
        s.add(AuditEvent(category=category, owner=owner, job_id=job_id))

    def _lock_gate(self, s):
        gate = s.execute(select(AdmissionLock).where(AdmissionLock.id == 1).with_for_update()).scalar_one_or_none()
        if gate is None:
            raise RuntimeError("Database migration/seed missing; initialize admission_lock first")

    def lookup_idempotency(self, owner: str, key: str, payload: dict) -> str | None:
        digest, _ = _fingerprint_and_bytes(payload)
        with self.sessions() as s:
            found = s.execute(select(Job).where(Job.owner == owner, Job.idempotency_key == key)).scalar_one_or_none()
            if found and found.payload_sha256 != digest:
                raise IdempotencyConflict("Idempotency key reused with a different request")
            return found.id if found else None

    def enqueue(self, owner: str, payload: dict, key: str | None = None) -> tuple[str, bool]:
        digest, raw = _fingerprint_and_bytes(payload)
        if key and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", key):
            raise ValueError("Idempotency-Key must contain 8–128 safe ASCII characters")
        with self.sessions.begin() as s:
            # Single PostgreSQL row serializes admission globally, including duplicate IDs
            # and pending quotas across all gateway replicas.
            self._lock_gate(s)
            if key:
                found = s.execute(select(Job).where(Job.owner == owner, Job.idempotency_key == key)).scalar_one_or_none()
                if found:
                    if found.payload_sha256 != digest:
                        raise IdempotencyConflict("Idempotency key reused with a different request")
                    return found.id, False
            active = ("queued", "running", "indeterminate")
            owner_count = s.scalar(select(func.count()).select_from(Job).where(Job.owner == owner, Job.status.in_(active)))
            model_count = s.scalar(select(func.count()).select_from(Job).where(Job.model == payload["model"], Job.status.in_(active)))
            if owner_count >= self.max_pending_per_owner or model_count >= self.max_pending_per_model:
                raise QueueFull("Outstanding job quota exceeded")
            job_id = uuid.uuid4().hex
            s.add(Job(id=job_id, owner=owner, model=payload["model"],
                      idempotency_key=key, payload_sha256=digest,
                      encrypted_input=self.fernet.encrypt(raw), status="queued"))
            self._audit(s, "job_accepted", owner, job_id)
            return job_id, True

    def claim(self, job_id: str, *, enabled_models: set[str] | None = None) -> tuple[dict, str] | None:
        with self.sessions.begin() as s:
            self._lock_gate(s)
            job = s.get(Job, job_id)
            if not job or job.status != "queued":
                return None
            if enabled_models is not None and job.model not in enabled_models:
                return None  # Keep previously queued jobs safe until explicitly enabled.
            # An uncertain GPU may still be executing. Reserve its model slot
            # until an operator verifies termination or a late result is saved.
            active_count = s.scalar(select(func.count()).select_from(Job).where(
                Job.model == job.model, Job.status.in_(["running", "indeterminate"])))
            if active_count >= self.max_running_per_model:
                return None
            data = json.loads(self.fernet.decrypt(job.encrypted_input))
            if isinstance(data.get("reference_audio"), str):
                data["reference_audio"] = base64.b64decode(data["reference_audio"], validate=True)
            job.status = "running"
            job.heartbeat_at = utcnow()
            job.updated_at = utcnow()
            self._audit(s, "job_claimed", job.owner, job.id)
            return data, job.model

    def heartbeat(self, job_id: str) -> bool:
        with self.sessions.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.status != "running":
                return False
            job.heartbeat_at = utcnow()
            job.updated_at = utcnow()
            return True

    def complete(self, job_id: str, audio: bytes, metadata: dict, inference_ms: float) -> None:
        if not audio or len(audio) > 32_000_000:
            raise ValueError("Generated WAV exceeds the 32 MB limit")
        encrypted_audio = self.fernet.encrypt(audio)
        with self.sessions.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.status != "running":
                raise InvalidState("Only running jobs can be completed")
            job.status = "completed"
            job.encrypted_input = None  # Minimize retention of voice samples and prompt text.
            job.encrypted_audio = encrypted_audio
            job.audio_metadata = json.dumps(metadata, separators=(",", ":"))
            job.inference_ms = round(inference_ms)
            job.updated_at = utcnow()
            self._audit(s, "job_completed", job.owner, job.id)

    def uncertain(self, job_id: str, *, code: str = "gpu_outcome_unknown") -> None:
        # Do NOT automatically requeue. GPU may have finished despite a lost acknowledgement.
        with self.sessions.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.status != "running":
                return
            job.status = "indeterminate"
            job.encrypted_input = None
            job.error_code = code
            job.updated_at = utcnow()
            self._audit(s, "job_indeterminate", job.owner, job.id)

    def _public(self, job: Job) -> dict:
        status = job.status
        if status == "running" and job.heartbeat_at:
            age = (utcnow() - _aware(job.heartbeat_at)).total_seconds()
            if age > self.stale_after_seconds:
                status = "indeterminate"  # Read-only advisory; late result remains allowed.
        info = {"job_id": job.id, "model": job.model, "status": status,
                "created_at": _aware(job.created_at).isoformat(),
                "updated_at": _aware(job.updated_at).isoformat()}
        if status == "completed":
            info.update(audio=json.loads(job.audio_metadata), inference_ms=job.inference_ms,
                        audio_url=f"/v1/audio/jobs/{job.id}/audio")
        if status == "indeterminate":
            info["warning"] = "GPU outcome unconfirmed; do not resubmit without manual reconciliation"
        return info

    def inspect(self, job_id: str, owner: str) -> dict | None:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            return None
        with self.sessions() as s:
            job = s.get(Job, job_id)
            return self._public(job) if job and job.owner == owner else None

    def audio(self, job_id: str, owner: str) -> bytes | None:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            return None
        with self.sessions() as s:
            job = s.get(Job, job_id)
            if not job or job.owner != owner:
                return None
            if job.status != "completed":
                raise TimeoutError("Audio not yet available")
            return self.fernet.decrypt(job.encrypted_audio)

    def queued_ids(self, limit: int = 30, model: str | None = None) -> list[str]:
        with self.sessions() as s:
            q = select(Job.id).where(Job.status == "queued")
            if model:
                q = q.where(Job.model == model)
            return list(s.scalars(q.order_by(Job.created_at).limit(min(limit, 100))))

    def safe_metrics(self) -> dict:
        with self.sessions() as s:
            rows = s.execute(select(Job.status, func.count()).group_by(Job.status)).all()
            return {"job_counts_by_state": {str(k): v for k, v in rows},
                    "scope": "persistent_database", "queued": self.queued_ids_count(s)}

    def queued_ids_count(self, s) -> int:
        return s.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))

    def close_after_manual_gpu_confirmation(self, job_id: str, *, acknowledgement: str) -> None:
        """Operator-only CLI action; NEVER called by the API or scheduled sweep."""
        if acknowledgement != "I_VERIFIED_GPU_IS_STOPPED":
            raise ValueError("Must explicitly acknowledge GPU termination after inspecting Modal logs")
        with self.sessions.begin() as s:
            self._lock_gate(s)
            job = s.get(Job, job_id)
            if not job or job.status not in {"running", "indeterminate"}:
                raise InvalidState("Only a running/indeterminate job may be manually closed")
            job.status = "failed"
            job.error_code = "operator_verified_stopped"
            job.encrypted_input = None
            job.updated_at = utcnow()
            self._audit(s, "operator_closed_job", job.owner, job.id)

    def delete_expired_terminal(self, retention_days: int = 7) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        from sqlalchemy import delete
        cutoff = utcnow() - timedelta(days=retention_days)
        with self.sessions.begin() as s:
            # Unknown GPU outcomes must never be erased automatically: their
            # records reserve concurrency until explicit operator reconciliation.
            result = s.execute(delete(Job).where(Job.status.in_(["completed", "failed"]), Job.updated_at < cutoff))
            return result.rowcount
