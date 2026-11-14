"""Maintain old runner ORM support after an earlier rename-based revision 0002.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("tts_jobs")}
    if "payload_sha256" not in columns:
        op.add_column("tts_jobs", sa.Column("payload_sha256", sa.String(64), nullable=True))
        connection.execute(sa.text(
            "UPDATE tts_jobs SET payload_sha256 = payload_fingerprint "
            "WHERE payload_sha256 IS NULL"))
    if "payload_fingerprint" not in columns:
        op.add_column("tts_jobs", sa.Column("payload_fingerprint", sa.String(64), nullable=True))

def downgrade():
    raise RuntimeError("Compatibility bridge cannot be downgraded in place.")
