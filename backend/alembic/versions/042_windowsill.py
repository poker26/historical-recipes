"""«Мой подоконник»: комнатные растения человека и напоминания об уходе

Зимой в лесу снимать нечего, и приложение держится на комнатных растениях: весь
топ-15 определений за октябрь 2026 года составили комнатные. Человек кладёт
растение на свой подоконник из результата определения или из карточки, видит,
что книги советуют делать с ним в этот сезон, и получает пуш при смене режима
ухода и раз в две недели.

Revision ID: 042_windowsill
Revises: 041_fungi_confused_with
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "042_windowsill"
down_revision = "041_fungi_confused_with"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "windowsill_plants",
        sa.Column("device_key", UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("device_key", "plant_id"),
    )
    op.create_table(
        "windowsill_prefs",
        sa.Column("device_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("reminders", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True)),
        sa.Column("last_season", sa.Text()),        # сезон последнего сезонного пуша
    )


def downgrade() -> None:
    op.drop_table("windowsill_prefs")
    op.drop_table("windowsill_plants")
