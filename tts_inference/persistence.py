"""Durable, encrypted job ledger. PostgreSQL is authoritative; SQLite is test-only.

The database contains encrypted prompts, reference WAVs and generated audio.
It does not implement a broker that guarantees exactly-once GPU execution:
uncertain running jobs are reported INDETERMINATE and never auto-retried.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import (
    BigInteger,
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
    update,
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
    # Preserve old runner column; new rows use HMAC in both columns.
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_fingerprint_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
    )
    encrypted_input: Mapped[bytes | None] = mapped_column(LargeBinary)
    encrypted_audio: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    audio_metadata: Mapped[str | None] = mapped_column(Text)
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    input_characters: Mapped[int | None] = mapped_column(Integer)
    worker_wall_ms: Mapped[int | None] = mapped_column(Integer)
    audio_validation_ms: Mapped[int | None] = mapped_column(Integer)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EstimatedGpuBudgetWindow(Base):
    """UTC-day global estimate, updated under the existing admission lock."""

    __tablename__ = "tts_estimated_gpu_budget_windows"
    utc_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AuditEvent(Base):
    __tablename__ = "tts_audit_events"
    __table_args__ = (
        Index("ix_tts_audit_job_owner_id", "job_id", "owner", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(100))
    job_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IdempotencyConflict(Exception):
    pass


class QueueFull(Exception):
    pass


class EstimatedBudgetExceeded(QueueFull):
    def __init__(self, seconds_until_next_day: int):
        self.retry_after_seconds = max(1, seconds_until_next_day)
        super().__init__("Estimated daily GPU admission budget exceeded")


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
                 max_running_per_model: int = 1, stale_after_seconds: int = 180,
                 estimated_gpu_micro_usd_per_job: int = 0,
                 maximum_estimated_gpu_micro_usd_per_day: int = 0):
        if not database_url or not encryption_key:
            raise ValueError("Database URL and a persistent Fernet key are required")
        if database_url.startswith("sqlite") and ":memory:" in database_url:
            self.engine = create_engine(database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            self.engine = create_engine(database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.fernet = Fernet(encryption_key)
        stable_encryption_key = (
            encryption_key.encode("ascii") if isinstance(encryption_key, str)
            else encryption_key
        )
        self._fingerprint_key = hmac.new(
            base64.urlsafe_b64decode(stable_encryption_key),
            b"text-to-speech-payload-fingerprint-version-2",
            hashlib.sha256,
        ).digest()
        self.max_pending_per_owner = max_pending_per_owner
        self.max_pending_per_model = max_pending_per_model
        self.max_running_per_model = max_running_per_model
        self.stale_after_seconds = stale_after_seconds
        for v in (max_pending_per_owner, max_pending_per_model, max_running_per_model, stale_after_seconds):
            if type(v) is not int or v < 1:
                raise ValueError("Storage limits must be positive integers")
        per_job = estimated_gpu_micro_usd_per_job
        daily_limit = maximum_estimated_gpu_micro_usd_per_day
        if (type(per_job) is not int or type(daily_limit) is not int
                or (per_job == 0) != (daily_limit == 0)
                or per_job < 0 or daily_limit < per_job or daily_limit > 1_000_000_000_000):
            raise ValueError("Invalid estimated GPU budget limits")
        self.estimated_gpu_micro_usd_per_job = per_job
        self.maximum_estimated_gpu_micro_usd_per_day = daily_limit

    def create_schema_for_tests(self):
        """For offline SQLite tests ONLY. Production uses Alembic migration."""
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as session:
            if session.get(AdmissionLock, 1) is None:
                session.add(AdmissionLock(id=1))

    def check_ready(self) -> bool:
        with self.sessions() as s:
            if s.get(AdmissionLock, 1) is None:
                return False
            if self.estimated_gpu_micro_usd_per_job:
                # Fail before admission if migration 0006 is missing.
                s.execute(select(EstimatedGpuBudgetWindow.utc_day).limit(1))
            return True

    def _audit(self, s, category: str, owner: str | None = None, job_id: str | None = None):
        s.add(AuditEvent(category=category, owner=owner, job_id=job_id))

    def _lock_gate(self, s):
        gate = s.execute(select(AdmissionLock).where(AdmissionLock.id == 1).with_for_update()).scalar_one_or_none()
        if gate is None:
            raise RuntimeError("Database migration/seed missing; initialize admission_lock first")

    def _keyed_fingerprint(self, owner: str, canonical_sha256: str) -> str:
        """HMAC of owner and canonical payload hash; needs the persistent Fernet secret."""
        message = b"tts-payload-v2\0" + owner.encode("utf-8") + b"\0" + bytes.fromhex(
            canonical_sha256
        )
        return hmac.new(self._fingerprint_key, message, hashlib.sha256).hexdigest()

    def migrate_legacy_payload_fingerprints(self, *, batch_size: int = 100) -> int:
        """Rekey legacy metadata in bounded transactions without loading audio.

        The PostgreSQL admission lock serializes each batch across replicas;
        interrupted migrations resume using the remaining version-one rows.
        """
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        converted = 0
        while True:
            with self.sessions.begin() as session:
                self._lock_gate(session)
                legacy_rows = session.execute(
                    select(Job.id, Job.owner, Job.payload_sha256)
                    .where(Job.payload_fingerprint_version == 1)
                    .order_by(Job.id)
                    .limit(batch_size)
                ).all()
                if not legacy_rows:
                    return converted
                for job_id, owner, legacy_sha in legacy_rows:
                    if not re.fullmatch(r"[0-9a-f]{64}", legacy_sha):
                        raise ValueError("Invalid legacy fingerprint; migration aborted")
                    digest = self._keyed_fingerprint(owner, legacy_sha)
                    session.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.payload_fingerprint_version == 1)
                        .values(
                            payload_sha256=digest,
                            payload_fingerprint=digest,
                            payload_fingerprint_version=2,
                        )
                    )
                    converted += 1

    @staticmethod
    def _verify_fingerprint_version(job: Job) -> None:
        if job.payload_fingerprint_version != 2:
            raise RuntimeError("Run the legacy fingerprint rekey before accepting requests")

    def lookup_idempotency(self, owner: str, key: str, payload: dict) -> str | None:
        legacy_sha256, _ = _fingerprint_and_bytes(payload)
        digest = self._keyed_fingerprint(owner, legacy_sha256)
        with self.sessions() as session:
            found = session.execute(
                select(Job).where(Job.owner == owner, Job.idempotency_key == key)
            ).scalar_one_or_none()
            if found:
                self._verify_fingerprint_version(found)
                if found.payload_fingerprint != digest:
                    raise IdempotencyConflict("Idempotency key reused with a different request")
            return found.id if found else None

    def enqueue(self, owner: str, payload: dict, key: str | None = None) -> tuple[str, bool]:
        legacy_sha256, raw = _fingerprint_and_bytes(payload)
        digest = self._keyed_fingerprint(owner, legacy_sha256)
        if key and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", key):
            raise ValueError("Idempotency-Key must contain 8–128 safe ASCII characters")
        with self.sessions.begin() as s:
            # Single PostgreSQL row serializes admission globally, including duplicate IDs
            # and pending quotas across all gateway replicas.
            self._lock_gate(s)
            if key:
                found = s.execute(select(Job).where(Job.owner == owner, Job.idempotency_key == key)).scalar_one_or_none()
                if found:
                    self._verify_fingerprint_version(found)
                    if found.payload_fingerprint != digest:
                        raise IdempotencyConflict("Idempotency key reused with a different request")
                    return found.id, False
            active = ("queued", "running", "indeterminate")
            owner_count = s.scalar(select(func.count()).select_from(Job).where(Job.owner == owner, Job.status.in_(active)))
            model_count = s.scalar(select(func.count()).select_from(Job).where(Job.model == payload["model"], Job.status.in_(active)))
            if owner_count >= self.max_pending_per_owner or model_count >= self.max_pending_per_model:
                raise QueueFull("Outstanding job quota exceeded")
            if self.estimated_gpu_micro_usd_per_job:
                # Fixed estimate, not actual Modal billing. PostgreSQL admission
                # lock makes this atomic across independent HTTP replicas.
                now_seconds = int(utcnow().timestamp())
                utc_day = now_seconds // 86400
                budget = s.get(EstimatedGpuBudgetWindow, utc_day)
                used = budget.reserved_micro_usd if budget is not None else 0
                reserved = used + self.estimated_gpu_micro_usd_per_job
                if reserved > self.maximum_estimated_gpu_micro_usd_per_day:
                    raise EstimatedBudgetExceeded((utc_day + 1) * 86400 - now_seconds)
                if budget is None:
                    s.add(EstimatedGpuBudgetWindow(
                        utc_day=utc_day, reserved_micro_usd=reserved
                    ))
                else:
                    budget.reserved_micro_usd = reserved
            job_id = uuid.uuid4().hex
            s.add(Job(id=job_id, owner=owner, model=payload["model"],
                      idempotency_key=key, payload_sha256=digest,
                      payload_fingerprint=digest, payload_fingerprint_version=2,
                      encrypted_input=self.fernet.encrypt(raw), status="queued",
                      input_characters=len(payload["input"])))
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
            claimed_at = utcnow()
            job.claimed_at = claimed_at
            job.heartbeat_at = claimed_at
            job.updated_at = claimed_at
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

    def complete(
        self, job_id: str, audio: bytes, metadata: dict, inference_ms: float, *,
        worker_wall_ms: float | None = None,
        audio_validation_ms: float | None = None,
    ) -> None:
        if not audio or len(audio) > 32_000_000:
            raise ValueError("Generated WAV exceeds the 32 MB limit")
        from tts_inference.wav import wave_metadata

        validated_metadata = wave_metadata(audio, analyze_quality=True)
        encrypted_audio = self.fernet.encrypt(audio)
        with self.sessions.begin() as s:
            job = s.get(Job, job_id)
            if not job or job.status != "running":
                raise InvalidState("Only running jobs can be completed")
            job.status = "completed"
            job.encrypted_input = None  # Minimize retention of voice samples and prompt text.
            job.encrypted_audio = encrypted_audio
            job.audio_metadata = json.dumps(validated_metadata, separators=(",", ":"))
            job.inference_ms = round(inference_ms)
            job.worker_wall_ms = round(worker_wall_ms) if worker_wall_ms is not None else None
            job.audio_validation_ms = (
                round(audio_validation_ms) if audio_validation_ms is not None else None
            )
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

    def _public(self, job: Job, events: list[dict] | None = None) -> dict:
        """Owner-scoped metrics only: never include input, reference voice or raw logs."""
        status = job.status
        if status == "running" and job.heartbeat_at:
            age = (utcnow() - _aware(job.heartbeat_at)).total_seconds()
            if age > self.stale_after_seconds:
                status = "indeterminate"  # Read-only warning; no automated GPU retry.
        created_at = _aware(job.created_at)
        updated_at = _aware(job.updated_at)
        claimed_at = _aware(job.claimed_at)
        completed = status == "completed"
        audio = json.loads(job.audio_metadata) if completed and job.audio_metadata else None
        audio_quality = audio.pop("audio_quality", None) if audio else None
        audio_seconds = audio.get("duration_seconds") if audio else None
        queue_wait_ms = (
            max(0, round((claimed_at - created_at).total_seconds() * 1000))
            if claimed_at is not None else None
        )
        total_ms = (
            max(0, round((updated_at - created_at).total_seconds() * 1000))
            if completed else None
        )
        realtime_factor = (
            round(job.inference_ms / (audio_seconds * 1000), 4)
            if completed and audio_seconds and job.inference_ms is not None else None
        )
        warning = (
            "GPU outcome unconfirmed; do not resubmit without manual reconciliation"
            if status == "indeterminate" else None
        )
        # An explicit allowlist prevents database-corrupted or untrusted errors
        # from becoming client-visible exception dumps.
        error_code = (
            job.error_code if job.error_code and re.fullmatch(r"[a-z0-9_]{1,48}", job.error_code)
            else "unspecified_error" if job.error_code else None
        )
        warnings = [warning] if warning else []
        if audio_quality and audio_quality.get("clipped_samples", 0) > 0:
            warnings.append("clipped_samples_present")
        info = {
            "job_id": job.id,
            "model": job.model,
            "status": status,
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "audio_quality": audio_quality,
            "timing": {
                "queue_wait_ms": queue_wait_ms,
                "worker_wall_ms": job.worker_wall_ms if completed else None,
                "inference_ms": job.inference_ms if completed else None,
                "audio_validation_ms": job.audio_validation_ms if completed else None,
                "model_load_ms": None,
                "total_ms": total_ms,
            },
            "usage": {
                "input_characters": job.input_characters,
                "input_tokens": None,
                "output_tokens": None,
                "audio_duration_seconds": audio_seconds,
                "realtime_factor": realtime_factor,
            },
            "diagnostics": {
                "request_id": job.id,
                "error_code": error_code if status in ("indeterminate", "failed") else None,
                "warnings": warnings,
                "events": events if events is not None else [],
            },
        }
        if completed:
            info.update(
                audio=audio,
                inference_ms=job.inference_ms,  # Backward-compatible legacy field.
                audio_url=f"/v1/audio/jobs/{job.id}/audio",
            )
        if warning:
            info["warning"] = warning
        return info

    def inspect(self, job_id: str, owner: str) -> dict | None:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            return None
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if not job or job.owner != owner:
                return None
            # Audit records contain only allowlisted event names and timestamps.
            categories = (
                "job_accepted", "job_claimed", "job_completed",
                "job_indeterminate", "operator_closed_job",
            )
            audit_rows = session.execute(
                select(AuditEvent.category, AuditEvent.created_at)
                .where(
                    AuditEvent.job_id == job.id,
                    AuditEvent.owner == owner,
                    AuditEvent.category.in_(categories),
                )
                .order_by(AuditEvent.id)
                .limit(20)
            ).all()
            return self._public(job, [
                {"event": category, "at": _aware(created_at).isoformat()}
                for category, created_at in audit_rows
            ])

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

    def queued_ids_with_available_model_slots(
        self, enabled_models: set[str], *, limit_per_model: int = 1,
    ) -> list[str]:
        """Find queued jobs fairly across enabled models without consuming blocked slots.

        Selection is advisory only. PostgreSQL claim() under the admission lock remains
        the authority for racing workers, and an uncertain GPU never gets retried.
        """
        if limit_per_model < 1:
            raise ValueError("limit_per_model must be positive")
        available_job_ids: list[str] = []
        with self.sessions() as session:
            for model_id in sorted(enabled_models):
                active_count = session.scalar(
                    select(func.count()).select_from(Job).where(
                        Job.model == model_id,
                        Job.status.in_(("running", "indeterminate")),
                    )
                )
                remaining_slots = self.max_running_per_model - active_count
                if remaining_slots <= 0:
                    continue
                query = (
                    select(Job.id)
                    .where(Job.status == "queued", Job.model == model_id)
                    .order_by(Job.created_at, Job.id)
                    .limit(min(limit_per_model, remaining_slots))
                )
                available_job_ids.extend(session.scalars(query).all())
        return available_job_ids

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
            expired = select(Job.id).where(
                Job.status.in_(["completed", "failed"]), Job.updated_at < cutoff
            )
            # Audit metadata has no FK cascade; remove it with each expired job
            # atomically. Never purge queued, running or uncertain job events.
            s.execute(delete(AuditEvent).where(AuditEvent.job_id.in_(expired)))
            result = s.execute(delete(Job).where(
                Job.status.in_(["completed", "failed"]), Job.updated_at < cutoff
            ))
            return result.rowcount
