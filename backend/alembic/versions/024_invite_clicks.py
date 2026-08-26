"""переходы по пригласительной ссылке — чтобы код не вводили руками

Приглашение работает так: человек делится ссылкой botanik.fun/i/<handle>.
Если приложение стоит — открывается оно и код приходит прямо в ссылке. Если не
стоит (обычный случай для приглашения!), открывается страница, а код теряется при
переходе в магазин. Поэтому запоминаем сам ПЕРЕХОД: отпечаток посетителя (хэш
IP+браузера) и код. Приложение при первом запуске спрашивает «меня не звали?» —
сервер ищет свежий переход с того же адреса.

Отпечаток — необратимый хэш с солью, живёт сутки (`GC` по created_at), после
использования помечается consumed. Ни адрес, ни User-Agent в открытом виде не
хранятся.

Revision ID: 024_invite_clicks
Revises: 023_invites
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "024_invite_clicks"
down_revision = "023_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quest_invite_clicks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text(), nullable=False),          # handle пригласившего
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("consumed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_invite_clicks_fp", "quest_invite_clicks", ["fingerprint", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_invite_clicks_fp", table_name="quest_invite_clicks")
    op.drop_table("quest_invite_clicks")
