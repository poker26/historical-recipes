"""журнал рассылок: одно письмо человеку — и никогда второго

Тактичность должна жить в механизме, а не в голове отправляющего. Первичный ключ
(campaign, device_key) физически не даёт отправить одну и ту же кампанию дважды,
даже если джоб запустят повторно или руками.

Revision ID: 025_push_campaign_log
Revises: 024_invite_clicks
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "025_push_campaign_log"
down_revision = "024_invite_clicks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_campaign_log",
        sa.Column("campaign", sa.String(60), primary_key=True),
        sa.Column("device_key", UUID(as_uuid=True), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(20)),        # sent | failed | skipped
        sa.Column("detail", sa.Text()),
    )
    op.create_index("ix_push_campaign_sent", "push_campaign_log", ["campaign", "sent_at"])


def downgrade() -> None:
    op.drop_index("ix_push_campaign_sent", table_name="push_campaign_log")
    op.drop_table("push_campaign_log")
