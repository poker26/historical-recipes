"""Essential oils as substance-entities + aromatherapy uses

Aromatherapy is a third source SHAPE: a book is organized BY OIL (a chapter per
named essential oil — лавандовое масло, масло чайного дерева — with its aroma,
therapeutic actions, application methods, dosage and contraindications), not by
plant and not by recipe. Modelling the oil as a first-class substance lets the
aromatherapy layer attach to it, and — because the oil bridges to its source
plant — those facts surface on the plant's herbarium card too.

Two additive tables:

- ``essential_oils`` — one row per NAMED oil. Bridges into the herbarium via
  ``plant_id`` (the source plant) and into the chemistry vocabulary via
  ``compound_id`` (the oil node under «эфирные масла»). Carries oil-specific
  descriptive fields (aroma profile, extraction method).
- ``essential_oil_uses`` — the aromatherapy therapeutic layer, the oil analog of
  ``plant_medicinal_uses``. It REUSES the existing controlled vocabularies:
  ``action_id`` -> ``medicinal_actions`` and ``indication_ids`` -> ``indications``,
  so a "what helps with anxiety" query unifies plant uses and oil uses on one
  normalized axis. ``original_text`` is REQUIRED (the same grounding guard).

Additive only — no existing table is touched.

Revision ID: 011_essential_oils
Revises: 010_plant_name_modern
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = "011_essential_oils"
down_revision = "010_plant_name_modern"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- the named-oil substance entity ---
    op.create_table(
        "essential_oils",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),       # canonical RU, e.g. "лавандовое масло"
        sa.Column("name_latin", sa.Text()),                              # pharmacopoeial Latin, e.g. "Oleum Lavandulae"
        sa.Column("synonyms", ARRAY(sa.String())),                       # alternate names/spellings
        # bridge into the herbarium — the plant the oil is distilled from.
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="SET NULL")),
        sa.Column("source_plant_raw", sa.Text()),                        # plant as written, before resolution
        # bridge into the chemistry vocabulary (the oil's node under «эфирные масла»).
        sa.Column("compound_id", UUID(as_uuid=True), sa.ForeignKey("compounds.id", ondelete="SET NULL")),
        sa.Column("part", sa.String(50)),                                # source part: цвет/лист/плод/кора/древесина...
        sa.Column("extraction", sa.String(60)),                          # дистилляция / отжим / экстракция / анфлераж
        sa.Column("aroma_profile", sa.Text()),                           # описание запаха / ноты
        sa.Column("description", sa.Text()),
        sa.Column("original_text", sa.Text()),                           # verbatim anchor for the oil entry
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_essential_oils_plant_id", "essential_oils", ["plant_id"])

    # --- the aromatherapy therapeutic layer (oil analog of plant_medicinal_uses) ---
    op.create_table(
        "essential_oil_uses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("oil_id", UUID(as_uuid=True), sa.ForeignKey("essential_oils.id", ondelete="CASCADE"), nullable=False),
        # REUSES the existing medicinal_actions / indications vocabularies.
        sa.Column("action_id", UUID(as_uuid=True), sa.ForeignKey("medicinal_actions.id", ondelete="SET NULL")),
        sa.Column("action_raw", sa.Text()),
        sa.Column("indications", sa.Text()),
        sa.Column("indication_ids", ARRAY(UUID(as_uuid=True)), server_default="{}"),
        sa.Column("application", sa.String(40)),    # ингаляция/массаж/ванна/аромалампа/компресс/внутрь/наружно
        sa.Column("dosage", sa.Text()),
        sa.Column("contraindications", sa.Text()),  # фототоксичность / беременность / аллергия — critical for oils
        sa.Column("original_text", sa.Text()),      # verbatim source — REQUIRED for grounding
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
        sa.Column("confidence", sa.Float()),
    )
    op.create_index("ix_essential_oil_uses_oil_id", "essential_oil_uses", ["oil_id"])
    # GIN index so "&& ARRAY[concept + descendants]" containment is fast, mirroring
    # the plant_medicinal_uses indication index.
    op.create_index(
        "ix_essential_oil_uses_indication_ids", "essential_oil_uses",
        ["indication_ids"], postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_essential_oil_uses_indication_ids", table_name="essential_oil_uses")
    op.drop_index("ix_essential_oil_uses_oil_id", table_name="essential_oil_uses")
    op.drop_table("essential_oil_uses")
    op.drop_index("ix_essential_oils_plant_id", table_name="essential_oils")
    op.drop_table("essential_oils")
