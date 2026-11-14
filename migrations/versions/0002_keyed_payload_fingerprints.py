"""Expand fingerprint schema while preserving old runner writes.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("tts_jobs", sa.Column("payload_fingerprint", sa.String(64), nullable=True))
    op.add_column("tts_jobs", sa.Column(
        "payload_fingerprint_version", sa.Integer(), nullable=False, server_default="1"))

def downgrade():
    raise RuntimeError("Restore a verified backup; fingerprint rekey is one-way.")
