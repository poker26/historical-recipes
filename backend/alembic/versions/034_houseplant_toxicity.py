"""опасность комнатного растения: чем, для кого и что делать

Слой ухода отвечает, как растение не убить. Этот отвечает на обратный вопрос:
чем растение опасно для человека, ребёнка и домашнего животного. Источник у
него отдельный и по правилу источников: книга по уходу не становится
источником по токсикологии оттого, что упомянула жгучий сок.

Замер 18 сентября показал, зачем таблица нужна: из 22 ядовитых комнатных родов
девяти нет в гербарии вовсе, а двенадцать наших книг по токсикологии не знают
ни одного комнатного ароидного — они про луга и пастбища.

Хранится то же, что в слое ухода: дословная цитата и страница. Совет «промыть
желудок» человек выполняет буквально, и выдуманного тут быть не может.

Revision ID: 034_houseplant_toxicity
Revises: 033_plant_origin
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "034_houseplant_toxicity"
down_revision = "033_plant_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "houseplant_toxicity",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("taxon_latin", sa.Text(), nullable=False),
        sa.Column("taxon_ru", sa.Text()),
        # Чем именно опасно: группа веществ, как её называет книга.
        sa.Column("toxin", sa.Text()),
        # Какие части ядовиты. Пустой список означает «книга не уточнила», а не
        # «всё растение безопасно».
        sa.Column("parts", ARRAY(sa.Text())),
        sa.Column("symptoms", sa.Text()),
        sa.Column("first_aid", sa.Text()),
        # Отдельно про тех, кто тянет листья в рот, не читая карточек.
        sa.Column("children", sa.Text()),
        sa.Column("pets", sa.Text()),
        # Насколько всё серьёзно: от раздражения кожи до угрозы жизни.
        sa.Column("severity", sa.Text()),      # irritant | toxic | dangerous
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("houseplant_source.id"), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("latin_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_houseplant_toxicity_taxon", "houseplant_toxicity", ["taxon_latin"])
    op.execute("""
        CREATE UNIQUE INDEX uq_houseplant_toxicity_quote
        ON houseplant_toxicity (taxon_latin, source_id, md5(quote))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_houseplant_toxicity_quote")
    op.drop_index("ix_houseplant_toxicity_taxon", table_name="houseplant_toxicity")
    op.drop_table("houseplant_toxicity")
