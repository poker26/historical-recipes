"""identifications: archive field photo-identification events

Creates the ``identifications`` table backing the field-data capture feature. Each
row records one photo identification from the Android client: the MinIO key of the
uploaded photo (bucket ``field-uploads``), the photo's EXIF, the user-granted
geolocation, the phone/OS, and the engine outcome (top candidate + full list).

Internal debugging/training capture — not exposed over MCP. All metadata columns
are nullable (geolocation/EXIF/device may be absent depending on permissions and
the source image).

Revision ID: 013_identifications
Revises: 012_inat_taxon_cache
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "013_identifications"
down_revision = "012_inat_taxon_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("photo_key", sa.Text(), nullable=True),
        sa.Column("engine", sa.String(length=30), nullable=True),
        sa.Column("organ", sa.String(length=20), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("geo_accuracy", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exif", JSONB(), nullable=True),
        sa.Column("device_model", sa.String(length=120), nullable=True),
        sa.Column("device_manufacturer", sa.String(length=120), nullable=True),
        sa.Column("os_version", sa.String(length=40), nullable=True),
        sa.Column("os_sdk", sa.Integer(), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("top_latin", sa.Text(), nullable=True),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("matched_plant_id", UUID(as_uuid=True),
                  sa.ForeignKey("plants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("matched_count", sa.Integer(), nullable=True),
        sa.Column("remaining_requests", sa.Integer(), nullable=True),
        sa.Column("candidates", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("identifications")
