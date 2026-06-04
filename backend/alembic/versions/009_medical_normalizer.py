"""Medical normalizer: indications vocabulary + grow medicinal_actions + links

Adds the medical counterpart of the compound vocabulary, on two axes:

- grows ``medicinal_actions`` with ``synonyms`` (to match raw action strings) and
  ``source_book_id`` provenance;
- adds a controlled, hierarchical ``indications`` vocabulary carrying an
  ``archaic`` array — the archaic→modern bridge (водянка→отёки, грудная
  жаба→стенокардия);
- adds ``indication_ids`` (ARRAY of vocab ids) on ``plant_medicinal_uses`` so the
  free-text ``indications`` field can be normalized to one or more concepts.

Additive only — existing rows keep ``action_id`` as-is and ``indication_ids``
empty until ``normalize_medical_uses`` runs.

Revision ID: 009_medical_normalizer
Revises: 008_inat_photos
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = "009_medical_normalizer"
down_revision = "008_inat_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- grow the existing medicinal_actions vocabulary ---
    op.add_column("medicinal_actions", sa.Column("synonyms", ARRAY(sa.String())))
    op.add_column(
        "medicinal_actions",
        sa.Column("source_book_id", UUID(as_uuid=True),
                  sa.ForeignKey("books.id", ondelete="SET NULL")),
    )

    # --- controlled, hierarchical vocabulary of indications (показания) ---
    op.create_table(
        "indications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("indications.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("name_modern", sa.Text()),
        sa.Column("synonyms", ARRAY(sa.String())),
        sa.Column("archaic", ARRAY(sa.String())),   # archaic→modern bridge
        sa.Column("system", sa.String(50)),
        sa.Column("definition", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )

    # --- normalized link: a use's free-text indications -> one or more concepts ---
    op.add_column(
        "plant_medicinal_uses",
        sa.Column("indication_ids", ARRAY(UUID(as_uuid=True)), server_default="{}"),
    )
    # GIN index so "&& ARRAY[concept + descendants]" containment is fast.
    op.create_index(
        "ix_plant_medicinal_uses_indication_ids", "plant_medicinal_uses",
        ["indication_ids"], postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_plant_medicinal_uses_indication_ids", table_name="plant_medicinal_uses")
    op.drop_column("plant_medicinal_uses", "indication_ids")
    op.drop_table("indications")
    op.drop_column("medicinal_actions", "source_book_id")
    op.drop_column("medicinal_actions", "synonyms")
