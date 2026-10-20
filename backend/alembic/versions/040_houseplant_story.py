"""рассказ о растении: родина, история, название, биология, применение, поверья

Слой ухода отвечает на вопрос «как держать», а этот на вопрос «что это за
растение». Каждый факт хранится вместе с дословной цитатой книги и страницей,
как и уход. Латынь может быть пустой: Верзилин и Золотницкий часто называют
растение только по-русски, и такой факт привязывается к карточке позже по
русскому имени.

Revision ID: 040_houseplant_story
Revises: 039_collective_quests
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "040_houseplant_story"
down_revision = "039_collective_quests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "houseplant_story",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("houseplant_source.id"), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("taxon_latin", sa.Text()),
        sa.Column("taxon_rank", sa.Text()),                 # genus | species
        sa.Column("taxon_ru", sa.Text()),
        sa.Column("latin_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        # homeland | history | name | biology | uses | lore | danger | lookalikes
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),      # мысль по-русски простыми словами
        sa.Column("quote", sa.Text(), nullable=False),     # дословно, на языке книги
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # Повторный прогон книги не должен класть тот же факт второй раз.
    op.execute("""
        CREATE UNIQUE INDEX uq_houseplant_story_quote ON houseplant_story
            (source_id, kind, md5(quote), coalesce(taxon_latin, ''), coalesce(taxon_ru, ''))
    """)
    op.execute("CREATE INDEX ix_houseplant_story_latin ON houseplant_story (lower(taxon_latin))")
    op.execute("CREATE INDEX ix_houseplant_story_ru ON houseplant_story (lower(taxon_ru))")


def downgrade() -> None:
    op.drop_table("houseplant_story")
