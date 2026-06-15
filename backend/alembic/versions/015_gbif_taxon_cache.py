"""gbif_taxon_cache: cached GBIF name-match results (external-truth backbone)

Backs the data-quality identity checks (identity.kingdom, identity.latin_unresolvable
and, later, name_vs_latin). One row per `_latin_key` with GBIF's match result
(matchType / kingdom / accepted name). NONE matches are cached too. Additive.

Revision ID: 015_gbif_taxon_cache
Revises: 014_data_quality_findings
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = "015_gbif_taxon_cache"
down_revision = "014_data_quality_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gbif_taxon_cache",
        sa.Column("latin_key", sa.String(length=120), primary_key=True),
        sa.Column("match_type", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("kingdom", sa.String(length=30), nullable=True),
        sa.Column("canonical", sa.Text(), nullable=True),
        sa.Column("rank", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("usage_key", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gbif_taxon_cache")
