"""приглашения: кто кого привёл в приложение

Третья линейка наград (идея Олега 2026-08-26) — за то, что человек приводит
других. Код приглашения = уже существующий публичный `handle` устройства, так что
новых секретов и таблиц не нужно: достаточно запомнить, кто кого позвал.

Revision ID: 023_invites
Revises: 022_personal_places
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "023_invites"
down_revision = "022_personal_places"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quest_devices", sa.Column("invited_by", UUID(as_uuid=True), nullable=True))
    op.add_column("quest_devices", sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_quest_devices_invited_by", "quest_devices", ["invited_by"])


def downgrade() -> None:
    op.drop_index("ix_quest_devices_invited_by", table_name="quest_devices")
    op.drop_column("quest_devices", "invited_at")
    op.drop_column("quest_devices", "invited_by")
