"""Opt-in CPU integration tests using disposable PostgreSQL and Redis services."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import redis.asyncio as redis
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.persistence import JobStore
from tts_inference.security import RatePolicy, RedisGuard


def integration_urls():
    database_url = os.environ.get("TTS_INTEGRATION_POSTGRESQL_URL")
    redis_url = os.environ.get("TTS_INTEGRATION_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("Set both TTS_INTEGRATION_*_URL variables for real-service tests")
    database_name = (make_url(database_url).database or "").lower()
    if "test" not in database_name and "validation" not in database_name:
        pytest.fail("Integration database must be named as a disposable test database")
    return database_url, redis_url


def speech(text):
    return prepare_request(
        SpeechRequest.model_validate({"model": "voxcpm2", "input": text})
    )


def test_real_postgresql_concurrent_admission_claim_and_owner_isolation():
    """Independent instances share admission and GPU slot locks."""
    database_url, _ = integration_urls()
    encryption_key = Fernet.generate_key()
    stores = [
        JobStore(database_url, encryption_key, max_running_per_model=1)
        for _ in range(4)
    ]
    assert all(store.check_ready() for store in stores), "Run Alembic migrations first"
    test_id = uuid4().hex
    owner = f"cpu-integration-{test_id}"
    payload = speech("Disposable concurrency test")

    def enqueue(index):
        return stores[index % len(stores)].enqueue(owner, payload, key=test_id)

    with ThreadPoolExecutor(max_workers=12) as workers:
        admissions = list(workers.map(enqueue, range(12)))
    assert len({job_id for job_id, _ in admissions}) == 1
    job_id = admissions[0][0]
    assert stores[0].inspect(job_id, owner)["status"] == "queued"
    assert stores[1].inspect(job_id, "different-owner") is None
    assert stores[2].lookup_idempotency(owner, test_id, payload) == job_id

    def claim(index):
        return stores[index % len(stores)].claim(
            job_id, enabled_models={"voxcpm2"}
        )

    with ThreadPoolExecutor(max_workers=12) as workers:
        claims = list(workers.map(claim, range(12)))
    assert sum(result is not None for result in claims) == 1

    stores[0].uncertain(job_id, code="remote_outcome_unknown")
    second_id, _ = stores[1].enqueue(
        owner, speech("Second disposable job"), key=f"{test_id}-second"
    )
    assert stores[2].claim(second_id, enabled_models={"voxcpm2"}) is None
    # This is simulated CPU work; no remote GPU has ever been invoked.
    stores[0].close_after_manual_gpu_confirmation(job_id, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")
    assert stores[1].claim(second_id, enabled_models={"voxcpm2"}) is not None
    stores[0].uncertain(second_id, code="test_simulation_complete")
    stores[0].close_after_manual_gpu_confirmation(second_id, acknowledgement="I_VERIFIED_GPU_IS_STOPPED")


def test_real_redis_distributed_limits_and_lockout():
    """The Redis Lua counters are atomic across independent connections."""
    _, redis_url = integration_urls()
    namespace = f"tts-cpu-integration-{uuid4().hex}"
    policy = RatePolicy(
        jobs_per_window=3, job_window_seconds=60,
        jobs_per_client_day=40, jobs_global_day=40,
    )

    async def exercise():
        clients = [redis.from_url(redis_url, decode_responses=True) for _ in range(3)]
        try:
            guards = [
                RedisGuard(client, namespace=namespace, policy=policy)
                for client in clients
            ]
            decisions = await asyncio.gather(
                *(guards[i % len(guards)].allow_job("same-owner") for i in range(12))
            )
            assert sum(decision.allowed for decision in decisions) == 3
            failures = [
                await guards[i % len(guards)].failure("untrusted-peer")
                for i in range(4)
            ]
            assert not failures[-1].allowed
            assert failures[-1].retry_after > 0
        finally:
            for client in clients:
                await client.aclose()

    asyncio.run(exercise())


def test_real_postgresql_global_estimated_budget_under_competing_gateways():
    """A disposable PostgreSQL row admits only the bounded number of jobs."""
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from tts_inference.persistence import (
        AuditEvent,
        EstimatedBudgetExceeded,
        EstimatedGpuBudgetWindow,
        Job,
    )

    database_url, _ = integration_urls()
    stores = [
        JobStore(
            database_url, Fernet.generate_key(),
            estimated_gpu_micro_usd_per_job=500_000,
            maximum_estimated_gpu_micro_usd_per_day=1_500_000,
        )
        for _ in range(4)
    ]
    assert all(store.check_ready() for store in stores)
    today = int(datetime.now(UTC).timestamp()) // 86400
    with stores[0].sessions.begin() as session:
        session.execute(delete(EstimatedGpuBudgetWindow).where(
            EstimatedGpuBudgetWindow.utc_day == today
        ))
    test_id = uuid4().hex

    def enqueue(index):
        try:
            job_id, created = stores[index % 4].enqueue(
                owner=f"budget-{test_id}", payload=speech(f"Budget admission {index}"),
                key=f"budget-{test_id}-{index}",
            )
            assert created
            return job_id
        except EstimatedBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(enqueue, range(6)))
    accepted = [job for job in results if job is not None]
    assert len(accepted) == 3
    with stores[0].sessions.begin() as session:
        day = session.get(EstimatedGpuBudgetWindow, today)
        assert day.reserved_micro_usd == 1_500_000
        # This is a dedicated, disposable integration database only.
        session.execute(delete(AuditEvent).where(AuditEvent.job_id.in_(accepted)))
        session.execute(delete(Job).where(Job.id.in_(accepted)))
        session.delete(day)
