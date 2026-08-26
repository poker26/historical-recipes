"""личные места: игра приходит туда, где человек снимает

Замер 2026-08-26: снимок хоть раз попал внутрь квест-места лишь у 16 из 196
устройств с геолокацией (8%). Воронка умирала до всякой механики — люди снимают
во дворе, по дороге, на даче, а не внутри размеченного парка. Личное место
заводится вокруг точки, где человек снимает регулярно; `owner_key` держит его
приватным (чужая дача не должна светиться пином на общей карте).

Revision ID: 022_personal_places
Revises: 021_ident_failure
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "022_personal_places"
down_revision = "021_ident_failure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quest_places", sa.Column("owner_key", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_quest_places_owner", "quest_places", ["owner_key"])


def downgrade() -> None:
    op.drop_index("ix_quest_places_owner", table_name="quest_places")
    op.drop_column("quest_places", "owner_key")
