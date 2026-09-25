"""журнал слияний и удалений карточек

Чистка идентичности сливает дубли в настоящие карточки и удаляет оболочки без
фактов. Оба действия необратимы для самой строки, поэтому каждое пишется сюда
целиком: что было за карточка (все поля строкой JSON), во что слилась или
почему удалена. По этому журналу карточку можно восстановить руками.

Revision ID: 036_card_identity_audit
Revises: 035_source_page_anchor
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "036_card_identity_audit"
down_revision = "035_source_page_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_identity_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("step", sa.String(24), nullable=False),      # dedup | shells | gbif | reid
        sa.Column("action", sa.String(24), nullable=False),    # merge | delete | relatin | retry
        sa.Column("plant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("name_latin", sa.Text()),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("target_name", sa.Text()),
        # Полная строка карточки до действия плюс число дочерних записей.
        sa.Column("payload", JSONB()),
    )
    op.create_index("ix_card_identity_audit_plant", "card_identity_audit", ["plant_id"])
    op.create_index("ix_card_identity_audit_step", "card_identity_audit", ["step", "action"])


def downgrade() -> None:
    op.drop_index("ix_card_identity_audit_step", table_name="card_identity_audit")
    op.drop_index("ix_card_identity_audit_plant", table_name="card_identity_audit")
    op.drop_table("card_identity_audit")
