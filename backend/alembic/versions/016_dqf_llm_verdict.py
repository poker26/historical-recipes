"""data_quality_findings: add LLM-adjudication columns

Adds the LLM verdict layer (RFC-data-quality-llm): a cached per-finding judgment
(real / false_positive / uncertain) with confidence, a suggested action and a
grounded reasoning string. All additive + nullable.

Revision ID: 016_dqf_llm_verdict
Revises: 015_gbif_taxon_cache
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "016_dqf_llm_verdict"
down_revision = "015_gbif_taxon_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_quality_findings", sa.Column("llm_verdict", sa.String(length=20), nullable=True))
    op.add_column("data_quality_findings", sa.Column("llm_confidence", sa.Float(), nullable=True))
    op.add_column("data_quality_findings", sa.Column("llm_action", sa.String(length=40), nullable=True))
    op.add_column("data_quality_findings", sa.Column("llm_reasoning", sa.Text(), nullable=True))
    op.add_column("data_quality_findings", sa.Column("llm_model", sa.String(length=60), nullable=True))
    op.add_column("data_quality_findings", sa.Column("llm_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ("llm_at", "llm_model", "llm_reasoning", "llm_action", "llm_confidence", "llm_verdict"):
        op.drop_column("data_quality_findings", col)
