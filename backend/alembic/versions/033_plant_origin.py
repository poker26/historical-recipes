"""карточка знает, откуда она: гербарий или слой комнатных

Комнатным растениям нужны собственные карточки: человек снял горшок, определитель
назвал вид, а приложение показывало пустоту, потому что в травниках такой карточки
нет и быть не может. Но смешивать их с гербарием нельзя: на карточках гербария
стоят квесты, витрина «Сейчас в лесу» и метрика «в корпусе», и восемьсот
тропических комнатных исказили бы всё это разом.

Поэтому у карточки появляется происхождение. ``herbarium`` — то, что пришло из
травников и участвует в прогулках и витринах. ``houseplant`` — карточка слоя
ухода: она отвечает на определение по фото, но в полевые механики не попадает.

Revision ID: 033_plant_origin
Revises: 032_houseplant_glossary
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "033_plant_origin"
down_revision = "032_houseplant_glossary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plants", sa.Column(
        "origin", sa.Text(), server_default="herbarium", nullable=False))
    op.create_index("ix_plants_origin", "plants", ["origin"])


def downgrade() -> None:
    op.drop_index("ix_plants_origin", table_name="plants")
    op.drop_column("plants", "origin")
