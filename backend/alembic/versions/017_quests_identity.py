"""quests identity: devices table + identifications.device_key

Phase 1 of the quests backend (docs/PLAN-quests-backend.md §1, RFC-quests §8a): a
silent, account-less device identity. A `devices` table keyed by a client-generated
UUID (no PII, no registration screen), and a `device_key` on `identifications` so
badge progress is attributable per device and server-verifiable from History.

Revision ID: 017_quests_identity
Revises: 016_dqf_llm_verdict
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "017_quests_identity"
down_revision = "016_dqf_llm_verdict"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quest_devices",  # not "devices": a pre-existing telemetry table owns that name
        sa.Column("device_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("nickname", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("identifications", sa.Column("device_key", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_identifications_device_key", "identifications", ["device_key"])


def downgrade() -> None:
    op.drop_index("ix_identifications_device_key", table_name="identifications")
    op.drop_column("identifications", "device_key")
    op.drop_table("quest_devices")
