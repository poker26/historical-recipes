"""с каким съедобным грибом путают ядовитый

Поле ``deadly_twin`` отвечает съедобному грибу: какой опасный гриб похож на него.
Ядовитому грибу нужен обратный ответ: за какой съедобный гриб его принимают
(строчок за сморчок, галерину за летний опёнок). Модель писала оба ответа в одно
поле, и у строчка осеннего появился «двойник» бледная поганка.

Revision ID: 041_fungi_confused_with
Revises: 040_houseplant_story
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "041_fungi_confused_with"
down_revision = "040_houseplant_story"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plants", sa.Column("confused_with", sa.Text()))


def downgrade() -> None:
    op.drop_column("plants", "confused_with")
