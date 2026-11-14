"""Add nullable job timing fields without breaking older readers or GPU runners.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tts_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tts_jobs", sa.Column("input_characters", sa.Integer(), nullable=True))
    op.add_column("tts_jobs", sa.Column("worker_wall_ms", sa.Integer(), nullable=True))
    op.add_column("tts_jobs", sa.Column("audio_validation_ms", sa.Integer(), nullable=True))


def downgrade():
    raise RuntimeError("Restore a verified backup rather than downgrading a live job ledger.")
