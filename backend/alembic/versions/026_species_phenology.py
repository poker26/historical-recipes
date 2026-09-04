"""фенология видов: когда растение реально находят

Квесты звали искать медуницу 4 сентября: сезонный фильтр стоял только на
именованных местах и отбирал виды по месяцу, когда их ФОТОГРАФИРУЮТ, а не когда
их можно узнать. Листья медуницы снимают до осени — она проходила. Кастомные и
личные квесты сезона не знали вовсе.

Здесь — доли наблюдений по месяцам из iNat (у медуницы неясной 40% в апреле и
2% в сентябре) и месяцы сбора надземных частей из корпуса. По ним фильтр
«что искать сейчас» решает, показывать ли вид в этом месяце.

Revision ID: 026_species_phenology
Revises: 025_push_campaign_log
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "026_species_phenology"
down_revision = "025_push_campaign_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "species_phenology",
        # тот же latin_key, что в quest_place_sets.species_set
        sa.Column("latin_key", sa.Text(), primary_key=True),
        sa.Column("latin", sa.Text()),
        sa.Column("inat_taxon_id", sa.Integer()),
        sa.Column("inat_n_obs", sa.Integer()),
        # доля наблюдений research-grade по месяцам, 12 чисел, сумма 1.0; NULL — iNat
        # вида не знает или наблюдений нет
        sa.Column("inat_months", ARRAY(sa.Float()), nullable=True),
        # месяцы сбора НАДЗЕМНЫХ частей по корпусу (корни/кора исключены: «корни
        # осенью» не значит, что растение осенью узнаваемо); 1..12
        sa.Column("corpus_months", ARRAY(sa.SmallInteger()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("species_phenology")
