"""страница источника у каждого факта

Факты корпуса знали книгу и дословную цитату, но не знали страницу: при
сборке книги страницы склеивались в один текст, и граница терялась. Сайт
хочет вести с карточки на оригинальную страницу скана, поэтому у каждой
таблицы фактов появляется номер страницы, оценка уверенности поиска и способ,
которым страница найдена (exact, fuzzy) или не найдена (none, short, nopages).
Способ хранится и для неудач, чтобы фоновый прогон не искал одно и то же
повторно.

У упоминаний (plant_book_mentions) колонка page_number уже была, но всегда
пустая; ей добавляются только оценка и способ.

Revision ID: 035_source_page_anchor
Revises: 034_houseplant_toxicity
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa

revision = "035_source_page_anchor"
down_revision = "034_houseplant_toxicity"
branch_labels = None
depends_on = None

# (таблица, колонка книги) — у всех есть original_text.
FACT_TABLES = [
    ("plant_medicinal_uses", "source_book_id"),
    ("recipes", "book_id"),
    ("plant_culinary_uses", "source_book_id"),
    ("plant_harvests", "source_book_id"),
    ("plant_habitats", "source_book_id"),
    ("plant_toxicities", "source_book_id"),
    ("essential_oil_uses", "source_book_id"),
]


def upgrade() -> None:
    for table, book_col in FACT_TABLES:
        op.add_column(table, sa.Column("source_page", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("anchor_score", sa.SmallInteger(), nullable=True))
        op.add_column(table, sa.Column("anchor_method", sa.String(12), nullable=True))
        # Панель «на этой странице упоминаются» ищет факты по книге и странице.
        op.create_index(f"ix_{table}_book_page", table, [book_col, "source_page"])
    op.add_column("plant_book_mentions", sa.Column("anchor_score", sa.SmallInteger(), nullable=True))
    op.add_column("plant_book_mentions", sa.Column("anchor_method", sa.String(12), nullable=True))
    op.create_index("ix_plant_book_mentions_book_page", "plant_book_mentions", ["book_id", "page_number"])


def downgrade() -> None:
    op.drop_index("ix_plant_book_mentions_book_page", table_name="plant_book_mentions")
    op.drop_column("plant_book_mentions", "anchor_method")
    op.drop_column("plant_book_mentions", "anchor_score")
    for table, _ in FACT_TABLES:
        op.drop_index(f"ix_{table}_book_page", table_name=table)
        op.drop_column(table, "anchor_method")
        op.drop_column(table, "anchor_score")
        op.drop_column(table, "source_page")
