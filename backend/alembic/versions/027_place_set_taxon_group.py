"""грибные наборы мест: у места может быть больше одного набора на окно

Решение владельца 4 сентября 2026: «грибы раньше зимы». Холостой прогон по всем
331 месту показал, что 119 из них получают в сентябре пять и больше грибов с
карточкой в корпусе — сезон продлевается на осень ещё до всякой зимы.

Но набор до сих пор был один на (место, окно), и он растительный. Грибной
набор нельзя класть в тот же пул: у растительного «Знатока места» кумулятивный
значок, и мухомор в нём обесценил бы редкость. Поэтому появляется признак
группы, ключ уникальности расширяется, а все читатели пула и пинов карты
фильтруют по группе «растения», пока грибной значок не построен.

Revision ID: 027_place_set_taxon_group
Revises: 026_species_phenology
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "027_place_set_taxon_group"
down_revision = "026_species_phenology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 'plants' | 'fungi'. Существующие строки — растительные, по умолчанию.
    op.add_column("quest_place_sets",
                  sa.Column("taxon_group", sa.Text(), nullable=False, server_default="plants"))
    op.drop_constraint("uq_place_window", "quest_place_sets", type_="unique")
    op.create_unique_constraint("uq_place_window_group", "quest_place_sets",
                                ["place_id", "window_label", "taxon_group"])


def downgrade() -> None:
    # Откат возможен только пока грибных наборов нет: старый ключ не вместит
    # две строки на (место, окно).
    op.execute("DELETE FROM quest_place_sets WHERE taxon_group <> 'plants'")
    op.drop_constraint("uq_place_window_group", "quest_place_sets", type_="unique")
    op.create_unique_constraint("uq_place_window", "quest_place_sets",
                                ["place_id", "window_label"])
    op.drop_column("quest_place_sets", "taxon_group")
