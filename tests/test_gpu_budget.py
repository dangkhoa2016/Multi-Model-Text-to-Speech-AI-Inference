"""CPU-only regression tests for fixed estimated GPU budget reservations."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.gpu_budget import parse_estimated_gpu_budget
from tts_inference.persistence import (
    EstimatedBudgetExceeded,
    EstimatedGpuBudgetWindow,
    JobStore,
)


def speech(text):
    return prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": text,
    }))


@pytest.mark.parametrize(
    ("per_job", "daily"),
    [
        ("1", None), (None, "10"), ("0", "10"),
        ("NaN", "10"), ("Infinity", "10"), ("2", "1"),
        ("not-numeric", "10"), ("-1", "10"), ("1", "-2"),
    ],
)
def test_invalid_gpu_budget_configuration_is_rejected(per_job, daily):
    with pytest.raises(ValueError):
        parse_estimated_gpu_budget(per_job, daily)


def test_estimated_budget_exact_decimal_and_opt_in():
    assert parse_estimated_gpu_budget(None, None) == (0, 0)
    assert parse_estimated_gpu_budget("0.000000001", "0.000002") == (1, 2)
    assert parse_estimated_gpu_budget("1.25", "3.50") == (1250000, 3500000)


def test_global_budget_is_atomic_with_idempotency_and_across_owners(tmp_path):
    db = f"sqlite+pysqlite:///{tmp_path / 'budget.sqlite'}"
    secret = Fernet.generate_key()
    estimate, limit = parse_estimated_gpu_budget("1.50", "3.00")
    stores = [
        JobStore(
            db, secret, estimated_gpu_micro_usd_per_job=estimate,
            maximum_estimated_gpu_micro_usd_per_day=limit,
        )
        for _ in range(2)
    ]
    stores[0].create_schema_for_tests()
    assert all(store.check_ready() for store in stores)
    first_id, created = stores[0].enqueue("alice", speech("First"), "budget-key-0001")
    assert created
    assert stores[1].enqueue("alice", speech("First"), "budget-key-0001") == (
        first_id, False,
    )
    stores[1].enqueue("bob", speech("Second"), "budget-key-0002")
    with pytest.raises(EstimatedBudgetExceeded) as captured:
        stores[0].enqueue("alice", speech("Third"), "budget-key-0003")
    assert 1 <= captured.value.retry_after_seconds <= 86400
    with stores[0].sessions() as session:
        record = session.scalars(select(EstimatedGpuBudgetWindow)).one()
        assert record.reserved_micro_usd == limit


@pytest.mark.parametrize("per_job,daily", [(0, 1), (1, 0), (-1, -1), (3, 2)])
def test_direct_job_store_invalid_budget_fails_closed(tmp_path, per_job, daily):
    with pytest.raises(ValueError):
        JobStore(
            f"sqlite+pysqlite:///{tmp_path / 'invalid.sqlite'}",
            Fernet.generate_key(),
            estimated_gpu_micro_usd_per_job=per_job,
            maximum_estimated_gpu_micro_usd_per_day=daily,
        )


def test_estimated_budget_is_not_refunded_by_job_failure(tmp_path):
    estimate, limit = parse_estimated_gpu_budget("2", "2")
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'retention.sqlite'}",
        Fernet.generate_key(), estimated_gpu_micro_usd_per_job=estimate,
        maximum_estimated_gpu_micro_usd_per_day=limit,
    )
    store.create_schema_for_tests()
    store.enqueue("alice", speech("First"), "budget-retention-0001")
    with pytest.raises(EstimatedBudgetExceeded):
        store.enqueue("alice", speech("Second"), "budget-retention-0002")
