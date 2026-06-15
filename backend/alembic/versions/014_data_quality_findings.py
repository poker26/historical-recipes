"""data_quality_findings: durable store for «линтер гербария» findings

Creates the ``data_quality_findings`` table backing the data-quality / consistency
checking subsystem (RFC-data-quality, Option A). Each row is one finding from a
pure-read validator, deduped on ``(check_id, entity_id)`` so re-running a sweep
updates rather than duplicates, with sticky human triage state and a ``stale``
state for findings whose problem is gone.

Additive + nullable; touches no existing table.

Revision ID: 014_data_quality_findings
Revises: 013_identifications
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "014_data_quality_findings"
down_revision = "013_identifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_quality_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("check_id", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=2), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("suggested_fix", JSONB(), nullable=True),
        sa.Column("auto_fixable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(length=12), server_default="open", nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_by", sa.String(length=60), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("check_id", "entity_id", name="uq_dqf_check_entity"),
    )
    op.create_index("ix_dqf_check_status", "data_quality_findings", ["check_id", "status"])
    op.create_index("ix_dqf_severity_status", "data_quality_findings", ["severity", "status"])


def downgrade() -> None:
    op.drop_index("ix_dqf_severity_status", table_name="data_quality_findings")
    op.drop_index("ix_dqf_check_status", table_name="data_quality_findings")
    op.drop_table("data_quality_findings")
