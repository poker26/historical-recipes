"""inat_taxon_cache: cache Latin → iNat Russian common name + taxon_id

Creates the ``inat_taxon_cache`` table backing the identify flow's Russian-name
enrichment. The identify path resolves every candidate species' Russian name from
iNaturalist (``preferred_common_name`` @ ``locale=ru``); this table caches that
resolution keyed on the genus+species latin key so repeated identifications don't
re-hit iNat (≤60 req/min budget) and the answer survives restarts.

Nullable ``name_ru`` / ``taxon_id`` deliberately store a DEFINITIVE "no Russian
name / no taxon" so we stop re-querying; only transient failures stay uncached.

Revision ID: 012_inat_taxon_cache
Revises: 011_essential_oils
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa

revision = "012_inat_taxon_cache"
down_revision = "011_essential_oils"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inat_taxon_cache",
        sa.Column("latin_key", sa.String(length=120), primary_key=True),
        sa.Column("name_ru", sa.Text(), nullable=True),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("inat_taxon_cache")
