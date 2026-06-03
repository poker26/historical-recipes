"""Culinary uses as a first-class plant fact

Adds ``plant_culinary_uses`` — the food-knowledge analog of
``plant_medicinal_uses`` — so edibility/preparation knowledge from foraging and
wild-food cookbooks (which parts are edible, how they're prepared as food,
seasonality/palatability caveats, edible-vs-toxic distinctions) becomes a
structured, queryable plant fact instead of free-text in ``description``.
Additive only — no change to existing rows.

Revision ID: 006_culinary
Revises: 005_compounds
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006_culinary"
down_revision = "005_compounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plant_culinary_uses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part", sa.String(50)),
        sa.Column("edibility", sa.String(20)),       # съедобно / условно-съедобно / несъедобно / ядовито
        sa.Column("preparation", sa.String(60)),     # сырым / отварить / сушить / квасить / жарить / мука
        sa.Column("use", sa.Text()),                 # what dish/role
        sa.Column("season", sa.Text()),
        sa.Column("caution", sa.Text()),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
        sa.Column("confidence", sa.Float()),
    )
    op.create_index("ix_plant_culinary_uses_plant_id", "plant_culinary_uses", ["plant_id"])


def downgrade() -> None:
    op.drop_index("ix_plant_culinary_uses_plant_id", table_name="plant_culinary_uses")
    op.drop_table("plant_culinary_uses")
