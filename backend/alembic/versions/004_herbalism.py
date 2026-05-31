"""Herbalism domain: enrich plants + medicinal use/compound/harvest/habitat/toxicity tables

Adds the structured store for the ``herbalism`` domain on top of the existing
``plants`` table, plus a pre-seeded ``medicinal_actions`` controlled vocabulary.

Revision ID: 004_herbalism
Revises: 003_source_format
Create Date: 2026-06-01
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = "004_herbalism"
down_revision = "003_source_format"
branch_labels = None
depends_on = None


# Starter vocabulary of medicinal actions, grouped by body system. Groups have
# parent_id=NULL; concrete actions point at their group. The LLM extractor
# normalizes free-text actions against this set (action_raw keeps the original).
_ACTION_GROUPS = [
    ("Действие на ЖКТ", "ЖКТ", [
        ("вяжущее", None),
        ("обволакивающее", None),
        ("слабительное", None),
        ("закрепляющее", "противопоносное"),
        ("ветрогонное", None),
        ("спазмолитическое", None),
        ("желчегонное", None),
        ("улучшающее пищеварение", "горечи"),
        ("противорвотное", None),
    ]),
    ("Мочегонное и выделительное", "выделение", [
        ("мочегонное", "диуретическое"),
        ("потогонное", None),
        ("глистогонное", "противоглистное"),
    ]),
    ("Сердечно-сосудистое", "ССС", [
        ("кардиотоническое", None),
        ("сосудорасширяющее", None),
        ("гипотензивное", "понижающее давление"),
        ("кровоостанавливающее", "гемостатическое"),
        ("кровоочистительное", None),
    ]),
    ("Действие на ЦНС", "ЦНС", [
        ("успокаивающее", "седативное"),
        ("снотворное", None),
        ("тонизирующее", None),
        ("обезболивающее", "анальгезирующее"),
        ("противосудорожное", None),
    ]),
    ("Действие на органы дыхания", "дыхание", [
        ("отхаркивающее", None),
        ("противокашлевое", None),
        ("смягчающее", None),
    ]),
    ("Противомикробное и наружное", "наружное", [
        ("противовоспалительное", None),
        ("антисептическое", "противомикробное"),
        ("ранозаживляющее", None),
        ("противогрибковое", None),
        ("инсектицидное", None),
    ]),
    ("Общее действие", "общее", [
        ("общеукрепляющее", None),
        ("жаропонижающее", "противолихорадочное"),
        ("витаминное", None),
        ("противоаллергическое", None),
        ("иммуностимулирующее", None),
    ]),
]


def _seed_medicinal_actions():
    rows = []
    for group_name, system, actions in _ACTION_GROUPS:
        gid = uuid.uuid4()
        rows.append({"id": gid, "parent_id": None, "name": group_name,
                     "name_modern": None, "system": system})
        for name, modern in actions:
            rows.append({"id": uuid.uuid4(), "parent_id": gid, "name": name,
                         "name_modern": modern, "system": system})
    actions_tbl = sa.table(
        "medicinal_actions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("parent_id", UUID(as_uuid=True)),
        sa.column("name", sa.Text()),
        sa.column("name_modern", sa.Text()),
        sa.column("system", sa.String(50)),
    )
    op.bulk_insert(actions_tbl, rows)


def upgrade() -> None:
    # --- enrich plants ---
    op.add_column("plants", sa.Column("family_latin", sa.Text()))
    op.add_column("plants", sa.Column("description", sa.Text()))
    op.add_column("plants", sa.Column("is_toxic", sa.Boolean(), nullable=False,
                                      server_default=sa.text("false")))

    # --- controlled vocabulary of medicinal actions ---
    op.create_table(
        "medicinal_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("medicinal_actions.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("name_modern", sa.Text()),
        sa.Column("system", sa.String(50)),
    )
    _seed_medicinal_actions()

    # --- medicinal uses ---
    op.create_table(
        "plant_medicinal_uses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part", sa.String(50)),
        sa.Column("action_id", UUID(as_uuid=True), sa.ForeignKey("medicinal_actions.id", ondelete="SET NULL")),
        sa.Column("action_raw", sa.Text()),
        sa.Column("indications", sa.Text()),
        sa.Column("preparation", sa.String(50)),
        sa.Column("dosage", sa.Text()),
        sa.Column("contraindications", sa.Text()),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
        sa.Column("confidence", sa.Float()),
    )
    op.create_index("ix_plant_medicinal_uses_plant_id", "plant_medicinal_uses", ["plant_id"])
    op.create_index("ix_plant_medicinal_uses_action_id", "plant_medicinal_uses", ["action_id"])

    # --- chemical compounds ---
    op.create_table(
        "plant_compounds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("compound", sa.Text(), nullable=False),
        sa.Column("compound_group", sa.String(60)),
        sa.Column("part", sa.String(50)),
        sa.Column("notes", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_compounds_plant_id", "plant_compounds", ["plant_id"])

    # --- harvest / collection notes ---
    op.create_table(
        "plant_harvests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part", sa.String(50)),
        sa.Column("season", sa.Text()),
        sa.Column("method", sa.Text()),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_harvests_plant_id", "plant_harvests", ["plant_id"])

    # --- habitats ---
    op.create_table(
        "plant_habitats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("region", sa.Text()),
        sa.Column("biotope", sa.Text()),
        sa.Column("status", sa.String(60)),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_habitats_plant_id", "plant_habitats", ["plant_id"])

    # --- toxicity ---
    op.create_table(
        "plant_toxicities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("toxic_parts", ARRAY(sa.String())),
        sa.Column("symptoms", sa.Text()),
        sa.Column("antidote", sa.Text()),
        sa.Column("severity", sa.String(30)),
        sa.Column("original_text", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_toxicities_plant_id", "plant_toxicities", ["plant_id"])


def downgrade() -> None:
    op.drop_table("plant_toxicities")
    op.drop_table("plant_habitats")
    op.drop_table("plant_harvests")
    op.drop_table("plant_compounds")
    op.drop_table("plant_medicinal_uses")
    op.drop_table("medicinal_actions")
    op.drop_column("plants", "is_toxic")
    op.drop_column("plants", "description")
    op.drop_column("plants", "family_latin")
