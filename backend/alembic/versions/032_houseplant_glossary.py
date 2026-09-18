"""уход комнатных: словарь ссылок книги («уход общий», «земельная смесь № 2»)

Головкин пишет уход ссылками на вводную часть: статья про вид говорит
«Содержат зимой при температуре 10—14 °C. Уход общий. Земельная смесь № 2», а
что стоит за словами «уход общий» и «земельная смесь № 2», сказано в начале
книги. Без разрешения этих ссылок карточка покажет человеку слова, за которыми
для него ничего нет.

Словарь живёт при источнике, потому что он у каждой книги свой: «смесь № 2» у
Головкина и «смесь № 2» у другого автора — разные рецепты земли.

Revision ID: 032_houseplant_glossary
Revises: 031_houseplant_latin_verified
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "032_houseplant_glossary"
down_revision = "031_houseplant_latin_verified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "houseplant_glossary",
        sa.Column("source_id", sa.Text(), sa.ForeignKey("houseplant_source.id"),
                  primary_key=True),
        # Ключ ровно в том виде, в каком его находит извлекатель в статье:
        # «уход общий», «земельная смесь № 2».
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),      # что это значит, словами книги
        sa.Column("quote", sa.Text(), nullable=False),     # дословно из вводной части
        sa.Column("page", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("houseplant_glossary")
