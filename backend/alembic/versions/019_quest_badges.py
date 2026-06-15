"""quest badges: place species-sets + issued badges

Phase 4/5 of the quests backend (docs/PLAN-quests-backend.md §4/§5, RFC-quests
§3a/§13). `quest_place_sets` = the characteristic species-set of a place × half-
month window (multi-year iNat aggregate, the badge TARGET). `quest_issued_badges`
= server-issued yearly badge instances with an ordinal (the scarcity record).

Revision ID: 019_quest_badges
Revises: 018_quest_places
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = "019_quest_badges"
down_revision = "018_quest_places"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quest_place_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("place_id", UUID(as_uuid=True), sa.ForeignKey("quest_places.id", ondelete="CASCADE")),
        sa.Column("window_label", sa.String(length=40)),   # 'first-half-may' (year-agnostic; multi-year set)
        sa.Column("species_set", ARRAY(sa.Text())),        # latin_keys, the characteristic set
        sa.Column("target", sa.Integer()),                 # round(0.6*|set|) clamped [5,15]
        sa.Column("obs_total", sa.Integer()),              # density signal
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("place_id", "window_label", name="uq_place_window"),
    )
    op.create_table(
        "quest_issued_badges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("badge_id", sa.Text()),                  # '{place}·{window}·{year}'
        sa.Column("device_key", UUID(as_uuid=True)),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ordinal", sa.Integer()),                # 'you are #N' for this badge_id
        sa.Column("window_closed", sa.Boolean(), server_default=sa.text("false")),
        sa.UniqueConstraint("badge_id", "device_key", name="uq_badge_device"),
    )
    op.create_index("ix_issued_badge_id", "quest_issued_badges", ["badge_id"])


def downgrade() -> None:
    op.drop_table("quest_issued_badges")
    op.drop_table("quest_place_sets")
