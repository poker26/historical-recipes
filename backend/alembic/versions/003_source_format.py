"""Add books.source_format (pdf | djvu | txt | docx)

Revision ID: 003_source_format
Revises: 002_wizard
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa

revision = "003_source_format"
down_revision = "002_wizard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("source_format", sa.String(10), nullable=False, server_default="pdf"),
    )


def downgrade() -> None:
    op.drop_column("books", "source_format")
