"""Kingdom discriminator on plants (растение | гриб)

Adds ``plants.kingdom`` so a mushroom field guide can be ingested through the
herbalism machinery (same table, collection, cross-linking) without polluting
the "plants" namespace: every row is honestly tagged растение / гриб, and a
future agent can ask for plants, fungi, or both unambiguously.

Additive only. The column is NOT NULL with a server default of ``растение``,
so existing rows backfill to растение in place — no data migration needed.

Revision ID: 007_kingdom
Revises: 006_culinary
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = "007_kingdom"
down_revision = "006_culinary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plants",
        sa.Column("kingdom", sa.String(20), nullable=False, server_default="растение"),
    )


def downgrade() -> None:
    op.drop_column("plants", "kingdom")
