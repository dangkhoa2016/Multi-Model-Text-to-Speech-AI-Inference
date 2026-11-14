"""Add a global UTC-day transactional GPU spend estimate ledger.

Revision ID: 0006
Revises: 0005

Admission estimates are not actual provider charges. Existing job data is
unchanged and no paid GPU action is involved in this schema upgrade.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tts_estimated_gpu_budget_windows",
        sa.Column("utc_day", sa.Integer(), primary_key=True),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of downgrading the job ledger.")
