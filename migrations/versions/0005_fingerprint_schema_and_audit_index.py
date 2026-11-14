"""Normalize legacy fingerprint nullability and index owner-scoped audit polling.

Revision ID: 0005
Revises: 0004

The earlier rename-based 0002 left reversed nullability. Do not edit
previous revisions: databases which have already applied them need a bridge.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {
        column["name"]: column
        for column in sa.inspect(connection).get_columns("tts_jobs")
    }
    if not {"payload_sha256", "payload_fingerprint"}.issubset(columns):
        raise RuntimeError("Upgrade through compatibility revision 0003 first")

    # Preserve the original digest for old writers and existing idempotency keys.
    # Legacy rename-based installations may have added this column as nullable.
    connection.execute(sa.text(
        "UPDATE tts_jobs SET payload_sha256 = payload_fingerprint "
        "WHERE payload_sha256 IS NULL AND payload_fingerprint IS NOT NULL"
    ))
    missing = connection.execute(sa.text(
        "SELECT COUNT(*) FROM tts_jobs WHERE payload_sha256 IS NULL"
    )).scalar_one()
    if missing:
        raise RuntimeError(
            "Cannot normalize fingerprint schema: legacy jobs lack both digests"
        )

    needs_required_sha = columns["payload_sha256"]["nullable"]
    needs_optional_fingerprint = not columns["payload_fingerprint"]["nullable"]
    if needs_required_sha or needs_optional_fingerprint:
        # SQLite requires a batch table rebuild; PostgreSQL uses ALTER COLUMN.
        with op.batch_alter_table("tts_jobs", schema=None) as batch:
            if needs_required_sha:
                batch.alter_column(
                    "payload_sha256", existing_type=sa.String(64), nullable=False
                )
            if needs_optional_fingerprint:
                batch.alter_column(
                    "payload_fingerprint", existing_type=sa.String(64), nullable=True
                )
    op.create_index(
        "ix_tts_audit_job_owner_id", "tts_audit_events",
        ["job_id", "owner", "id"],
    )


def downgrade():
    raise RuntimeError("Restore a verified backup rather than downgrading the job ledger.")
