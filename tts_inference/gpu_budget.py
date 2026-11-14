"""Optional transactional daily GPU cost proxy; never a provider billing cap."""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal, InvalidOperation

MICRO_USD = Decimal(1000000)
MAX_ESTIMATE_MICRO_USD = 1_000_000_000_000


def parse_estimated_gpu_budget(
    usd_per_job: str | None, daily_usd_limit: str | None
) -> tuple[int, int]:
    """Require both estimates or neither, using exact decimal arithmetic."""
    if usd_per_job is None and daily_usd_limit is None:
        return 0, 0
    if not usd_per_job or not daily_usd_limit:
        raise ValueError("Both estimated GPU budget settings must be configured")

    def convert(value: str, label: str) -> int:
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError(f"{label} must be a positive finite USD amount") from error
        if not amount.is_finite() or amount <= 0:
            raise ValueError(f"{label} must be a positive finite USD amount")
        if amount > Decimal(MAX_ESTIMATE_MICRO_USD) / MICRO_USD:
            raise ValueError(f"{label} is outside the supported USD range")
        converted = int((amount * MICRO_USD).to_integral_value(rounding=ROUND_CEILING))
        if converted < 1 or converted > MAX_ESTIMATE_MICRO_USD:
            raise ValueError(f"{label} is outside the supported USD range")
        return converted

    per_job = convert(usd_per_job, "Estimated GPU USD per job")
    daily = convert(daily_usd_limit, "Maximum estimated GPU USD per UTC day")
    if per_job > daily:
        raise ValueError("Per-job GPU estimate cannot exceed the daily budget")
    return per_job, daily
