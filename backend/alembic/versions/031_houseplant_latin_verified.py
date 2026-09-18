"""уход комнатных: отметка о том, что имя подтверждено GBIF

Распознавание книг портит латынь, и часть имён внешний справочник не узнаёт
вовсе: «Stepttanotis» и даже «Stefanotis» GBIF не находит ни точно, ни нечётко.
Выдумывать за него нельзя, выбрасывать жалко — уход в такой строке настоящий.
Поэтому строка живёт, но помечена непроверенной, и карточка на неё не
опирается, пока имя не починили.

Revision ID: 031_houseplant_latin_verified
Revises: 030_houseplant_care_unique
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "031_houseplant_latin_verified"
down_revision = "030_houseplant_care_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("houseplant_care", sa.Column(
        "latin_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_index("ix_houseplant_care_verified", "houseplant_care", ["latin_verified"])


def downgrade() -> None:
    op.drop_index("ix_houseplant_care_verified", table_name="houseplant_care")
    op.drop_column("houseplant_care", "latin_verified")
