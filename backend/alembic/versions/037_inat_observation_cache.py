"""кэш ответов iNaturalist «где встречается»

Страница карточки на сайте спрашивает наблюдения iNaturalist при каждой сборке, а
поисковые роботы обходят тысячи карточек. 30.09.2026 бэкенд делал около 1 400
запросов к iNaturalist за 15 минут, и 73% из них получали отказ 429. Ответ по виду и
месту теперь хранится здесь: наблюдения неделю, разрешённое место 90 дней. Кэш в
памяти процесса не годится: воркер перезапускается каждые 4 000 запросов.

Revision ID: 037_inat_observation_cache
Revises: 036_card_identity_audit
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "037_inat_observation_cache"
down_revision = "036_card_identity_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inat_observation_cache",
        sa.Column("key", sa.Text(), primary_key=True),  # obs:{вид, место, круг, число} | place:{запрос}
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("inat_observation_cache")
