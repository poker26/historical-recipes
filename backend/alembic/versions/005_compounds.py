"""Compound vocabulary + PlantCompound.compound_id (reference-normalizer pipeline)

Adds a controlled, hierarchical ``compounds`` vocabulary (the chemistry analog
of ``medicinal_actions``) and a nullable ``compound_id`` FK on
``plant_compounds`` so the free-text ``compound`` strings can be normalized to a
single vocabulary entry. Additive only — existing rows keep ``compound_id``
NULL until a phytochemistry reference is ingested and ``normalize_corpus`` runs.

Revision ID: 005_compounds
Revises: 004_herbalism
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = "005_compounds"
down_revision = "004_herbalism"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- controlled vocabulary of chemical constituents ---
    op.create_table(
        "compounds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("compounds.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("name_latin", sa.Text()),
        sa.Column("synonyms", ARRAY(sa.String())),
        sa.Column("compound_class", sa.String(60)),
        sa.Column("definition", sa.Text()),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )

    # --- normalized link from a plant's raw compound string to the vocabulary ---
    op.add_column(
        "plant_compounds",
        sa.Column("compound_id", UUID(as_uuid=True),
                  sa.ForeignKey("compounds.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_compounds_compound_id", "plant_compounds", ["compound_id"])


def downgrade() -> None:
    op.drop_index("ix_plant_compounds_compound_id", table_name="plant_compounds")
    op.drop_column("plant_compounds", "compound_id")
    op.drop_table("compounds")
