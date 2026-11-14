"""Read-only CPU operations snapshot, without client IDs, job IDs or content."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from .persistence import EstimatedGpuBudgetWindow, Job, utcnow


def collect_operations_snapshot(
    store, *, queued_age_seconds: int = 600, running_age_seconds: int = 180
) -> dict:
    """Summarize unresolved workloads; never update state or launch workers."""
    if (type(queued_age_seconds) is not int or queued_age_seconds < 1
            or type(running_age_seconds) is not int or running_age_seconds < 1):
        raise ValueError("Operations monitor thresholds must be positive integers")
    now = utcnow()
    with store.sessions() as session:
        totals = dict(session.execute(
            select(Job.status, func.count()).group_by(Job.status)
        ).all())
        delayed_queued = session.scalar(select(func.count()).select_from(Job).where(
            Job.status == "queued",
            Job.created_at < now - timedelta(seconds=queued_age_seconds),
        ))
        stale_running = session.scalar(select(func.count()).select_from(Job).where(
            Job.status == "running",
            or_(
                Job.heartbeat_at.is_(None),
                Job.heartbeat_at < now - timedelta(seconds=running_age_seconds),
            ),
        ))
        today = int(now.timestamp()) // 86400
        reserved = session.get(EstimatedGpuBudgetWindow, today)
    warnings = []
    if delayed_queued:
        warnings.append("delayed_queued")
    if stale_running:
        warnings.append("stale_running")
    if totals.get("indeterminate", 0):
        warnings.append("indeterminate")
    return {
        "status": "attention" if warnings else "ok",
        "checked_at": now.isoformat(),
        "job_counts_by_state": totals,
        "delayed_queued_count": delayed_queued,
        "stale_running_count": stale_running,
        "estimated_gpu_usd_reserved_today": (
            f"{Decimal(reserved.reserved_micro_usd) / Decimal(1_000_000):.6f}"
            if reserved is not None else "0.000000"
        ),
        "warnings": warnings,
    }
