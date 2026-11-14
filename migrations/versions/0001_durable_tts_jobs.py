"""Initial encrypted durable TTS queue and audit state.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("admission_lock", sa.Column("id", sa.Integer(), primary_key=True))
    op.execute(sa.text("INSERT INTO admission_lock (id) VALUES (1)"))
    op.create_table("tts_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("model", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("encrypted_input", sa.LargeBinary()),
        sa.Column("encrypted_audio", sa.LargeBinary()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("audio_metadata", sa.Text()),
        sa.Column("inference_ms", sa.Integer()),
        sa.Column("error_code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner", "idempotency_key", name="uq_tts_owner_idempotency"),
    )
    op.create_index("ix_tts_job_state_model", "tts_jobs", ["status", "model", "created_at"])
    op.create_index("ix_tts_job_owner_state", "tts_jobs", ["owner", "status"])
    op.create_table("tts_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("owner", sa.String(100)),
        sa.Column("job_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("tts_audit_events")
    op.drop_index("ix_tts_job_owner_state", table_name="tts_jobs")
    op.drop_index("ix_tts_job_state_model", table_name="tts_jobs")
    op.drop_table("tts_jobs")
    op.drop_table("admission_lock")
