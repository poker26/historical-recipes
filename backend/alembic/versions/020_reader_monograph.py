"""reader monograph: precomputed grounded review-gated monograph per plant

Layer 2 of the data work (docs/RFC-reader-monograph.md). One row per plant holds
the capped, prose, vetted «reader monograph» that `?view=field` serves to humans.
`generated_from_hash` makes regeneration idempotent (only plants whose distilled
input changed are re-generated). Default view + MCP get_plant stay RAW (agents).

Revision ID: 020_reader_monograph
Revises: 019_quest_badges
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "020_reader_monograph"
down_revision = "019_quest_badges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plant_reader_monograph",
        sa.Column("plant_id", UUID(as_uuid=True),
                  sa.ForeignKey("plants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("monograph", JSONB(), nullable=False),
        sa.Column("generated_from_hash", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=60)),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("gate_findings", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # served only when reviewed → partial index keeps the serve-time lookup tight
    op.create_index("ix_reader_monograph_reviewed", "plant_reader_monograph", ["reviewed"])


def downgrade() -> None:
    op.drop_table("plant_reader_monograph")
