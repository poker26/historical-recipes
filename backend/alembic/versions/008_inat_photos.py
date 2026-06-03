"""iNaturalist enrichment: canonical photo + taxon link on plants

Adds the columns that an iNaturalist enrichment pass writes per species:
``inat_taxon_id`` (the bridge key, reused later for "find nearby observations"),
a canonical ``photo_url`` plus its ``photo_attribution``/``photo_license``/
``photo_source`` (we only store photos whose license permits our use, and we
always display attribution), and ``inat_synced_at`` so the pass is resumable
(skip already-synced rows) and re-runnable (refresh stale ones).

Additive only — all columns nullable, existing rows untouched.

Revision ID: 008_inat_photos
Revises: 007_kingdom
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = "008_inat_photos"
down_revision = "007_kingdom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("inat_taxon_id", sa.Integer(), nullable=True))
    op.add_column("plants", sa.Column("photo_url", sa.Text(), nullable=True))
    op.add_column("plants", sa.Column("photo_attribution", sa.Text(), nullable=True))
    op.add_column("plants", sa.Column("photo_license", sa.String(40), nullable=True))
    op.add_column("plants", sa.Column("photo_source", sa.String(30), nullable=True))
    op.add_column("plants", sa.Column("inat_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ("inat_synced_at", "photo_source", "photo_license", "photo_attribution", "photo_url", "inat_taxon_id"):
        op.drop_column("plants", col)
