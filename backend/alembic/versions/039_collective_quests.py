"""общие квесты «Подоконник» и «Природа» (docs/RFC-collective-quests.md)

Квест собирают все пользователи вместе: 15 позиций, каждая задаёт род растения или гриба,
позицию закрывает первый снимок этого рода. Застрявшая позиция через 3 дня заменяется.
Когда закрыта последняя позиция, квест собран и сразу открывается следующий того же вида.

Revision ID: 039_collective_quests
Revises: 038_inat_budget
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "039_collective_quests"
down_revision = "038_inat_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collective_quests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.Text(), nullable=False),                 # home | nature
        sa.Column("number", sa.Integer(), nullable=False),            # «Подоконник №3»
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),   # open | closed
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kind", "number", name="uq_collective_kind_number"),
    )
    # Открытый квест каждого вида только один.
    op.create_index("uq_collective_open_kind", "collective_quests", ["kind"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "collective_slots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("quest_id", UUID(as_uuid=True), sa.ForeignKey("collective_quests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("genus", sa.Text(), nullable=False),                # латинский род: Hoya
        sa.Column("name_ru", sa.Text(), nullable=False),              # подпись: «Хойя»
        sa.Column("plant_id", UUID(as_uuid=True)),                    # карточка самого частого вида рода
        sa.Column("photo_url", sa.Text()),                            # эталонное фото этой карточки
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),   # open | closed | replaced
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", UUID(as_uuid=True)),                   # device_key
        sa.Column("identification_id", UUID(as_uuid=True)),           # снимок, закрывший позицию
        sa.Column("close_order", sa.Integer()),
        sa.Column("points", sa.Integer()),
        sa.Column("photo_hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("quest_id", "genus", name="uq_collective_slot_genus"),
    )
    op.create_index("ix_collective_slots_quest", "collective_slots", ["quest_id"])
    op.create_index("ix_collective_slots_open_genus", "collective_slots", ["genus"],
                    postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "collective_finds",
        sa.Column("slot_id", UUID(as_uuid=True), sa.ForeignKey("collective_slots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("device_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("identification_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "collective_awards",
        sa.Column("quest_id", UUID(as_uuid=True), sa.ForeignKey("collective_quests.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("device_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("finisher", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_collective_awards_device", "collective_awards", ["device_key"])

    op.create_table(
        "collective_reports",
        sa.Column("slot_id", UUID(as_uuid=True), sa.ForeignKey("collective_slots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("reporter", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_table("collective_reports")
    op.drop_index("ix_collective_awards_device", table_name="collective_awards")
    op.drop_table("collective_awards")
    op.drop_table("collective_finds")
    op.drop_index("ix_collective_slots_open_genus", table_name="collective_slots")
    op.drop_index("ix_collective_slots_quest", table_name="collective_slots")
    op.drop_table("collective_slots")
    op.drop_index("uq_collective_open_kind", table_name="collective_quests")
    op.drop_table("collective_quests")
