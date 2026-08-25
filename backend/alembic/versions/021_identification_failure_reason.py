"""identifications: почему определение не состоялось

Замер 2026-08-26: 7.1% снимков уходят без единого кандидата, и в архиве у них
`candidates = null` — без всякой причины. Мы не отличаем исчерпанную квоту
PlantNet от таймаута и от честного «совпадений нет», а это три разные починки.
Пишем причину рядом со снимком.

Revision ID: 021_ident_failure
Revises: 020_reader_monograph
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "021_ident_failure"
down_revision = "020_reader_monograph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("identifications", sa.Column("failure_reason", sa.String(32), nullable=True))
    op.add_column("identifications", sa.Column("failure_detail", sa.Text(), nullable=True))
    op.create_index("ix_identifications_failure_reason", "identifications", ["failure_reason"])


def downgrade() -> None:
    op.drop_index("ix_identifications_failure_reason", table_name="identifications")
    op.drop_column("identifications", "failure_detail")
    op.drop_column("identifications", "failure_reason")
