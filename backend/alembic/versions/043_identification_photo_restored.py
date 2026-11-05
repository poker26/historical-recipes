"""Когда снимок определения вернулся в архив с телефона

Архив снимков (field-uploads) погиб вместе с server 2 в аварии Бегета (11.2026):
7700 фото с 07.06 по 31.10. Приложение 2.6.0 тихо присылает их с телефона в
POST /api/identify/{id}/photo. Время возврата нужно дашборду «Что растёт», чтобы
видеть, сколько уже вернулось и как быстро.

Revision ID: 043_identification_photo_restored
Revises: 042_windowsill
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = "043_identification_photo_restored"
down_revision = "042_windowsill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("identifications",
                  sa.Column("photo_restored_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_identifications_photo_restored_at", "identifications", ["photo_restored_at"])


def downgrade() -> None:
    op.drop_index("ix_identifications_photo_restored_at", table_name="identifications")
    op.drop_column("identifications", "photo_restored_at")
