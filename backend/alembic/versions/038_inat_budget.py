"""общий бюджет запросов к iNaturalist и время ответа определения

Лимит iNaturalist один на IP сервера и общий для бэкенда и диспетчера. 29–30.09.2026 его
съели обход сайта роботами и фоновые шаги чистки, и до пользователей не дошла треть
определений за день. inat_rate_state держит общую паузу после отказа 429 (её видят все
процессы) и ведро жетонов для фоновых задач; inat_rate_events пишет каждый отказ, а
identifications.elapsed_ms время ответа определения: по ним Grafana поднимает тревогу.

Revision ID: 038_inat_budget
Revises: 037_inat_observation_cache
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "038_inat_budget"
down_revision = "037_inat_observation_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inat_rate_state",
        sa.Column("name", sa.Text(), primary_key=True),          # shared | background
        sa.Column("tokens", sa.Float(), nullable=False, server_default="5"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True)),
    )
    op.execute("INSERT INTO inat_rate_state (name) VALUES ('shared'), ('background')")
    op.create_table(
        "inat_rate_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.Text()),                           # кто получил отказ: identify, nearby, site, drift…
        sa.Column("status", sa.Integer()),
        sa.Column("retry_after", sa.Integer()),
    )
    op.create_index("ix_inat_rate_events_at", "inat_rate_events", ["at"])
    op.add_column("identifications", sa.Column("elapsed_ms", sa.Integer()))


def downgrade() -> None:
    op.drop_column("identifications", "elapsed_ms")
    op.drop_index("ix_inat_rate_events_at", table_name="inat_rate_events")
    op.drop_table("inat_rate_events")
    op.drop_table("inat_rate_state")
