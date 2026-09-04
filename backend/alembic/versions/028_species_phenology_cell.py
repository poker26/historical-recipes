"""фенология по ячейкам регионов: климат из наблюдений места, а не из линейки

Гистограммы в species_phenology собраны по всему миру. Для лета это неважно —
кривые «мир» и «Россия» у крапивы, медуницы, рябины совпадают, — но зимний хвост
таблицы сплошь субтропики и ботсады: в январе порог 5% «проходят» 45 видов, и
это Сочи, а не Пенза. Решение владельца 4 сентября 2026: широта как признак
климата не годится — «долгота в России играет огромную роль, Сахалин и Сочи на
одной широте». Поэтому сезон считается по ячейке 2°×2° вокруг места: гистограмма
iNat принимает границы прямоугольника, справочник климата не нужен.

Ячейка — floor(lat/2)*2, floor(lng/2)*2 (юго-западный угол). Замер: 75 ячеек с
квест-местами, 7928 пар ячейка×вид. Глобальная строка остаётся запасной: где
наблюдений в ячейке мало, читатель берёт её.

Revision ID: 028_species_phenology_cell
Revises: 027_place_set_taxon_group
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "028_species_phenology_cell"
down_revision = "027_place_set_taxon_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "species_phenology_cell",
        sa.Column("latin_key", sa.Text(), primary_key=True),
        sa.Column("cell_lat", sa.SmallInteger(), primary_key=True),   # floor(lat/2)*2
        sa.Column("cell_lng", sa.SmallInteger(), primary_key=True),   # floor(lng/2)*2
        sa.Column("inat_n_obs", sa.Integer()),
        # доли research-grade наблюдений по месяцам ВНУТРИ ячейки, 12 чисел, сумма 1;
        # NULL — наблюдений в ячейке нет
        sa.Column("inat_months", ARRAY(sa.Float()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_phenology_cell_cell", "species_phenology_cell", ["cell_lat", "cell_lng"])


def downgrade() -> None:
    op.drop_index("ix_phenology_cell_cell", table_name="species_phenology_cell")
    op.drop_table("species_phenology_cell")
