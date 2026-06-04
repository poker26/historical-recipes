"""Plant.name_modern: modern Russian common name from iNaturalist

Adds a single nullable column ``plants.name_modern``. It holds the modern Russian
common name (iNat ``preferred_common_name`` at ``locale=ru``) resolved during the
iNaturalist enrichment pass — EXTERNAL data, parallel to the iNat photo, kept
distinct from the book-grounded ``name`` / ``names_historical``.

Many plants ingested from the old plant dictionary have a great Latin name and a
long folk-name list but no modern Russian name; some even carry the bare Latin in
``name``. This column gives them their modern name without overwriting the
source-grounded headword. Additive only.

Revision ID: 010_plant_name_modern
Revises: 009_medical_normalizer
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "010_plant_name_modern"
down_revision = "009_medical_normalizer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("name_modern", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("plants", "name_modern")
