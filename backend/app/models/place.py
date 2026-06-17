import uuid
from datetime import datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuestPlace(Base):
    """A named OSM place (park/forest/reserve) with a polygon boundary, used for
    the place×season badges (RFC-quests §3a). The PostGIS `geom` column is managed
    by raw SQL / PostGIS functions (ST_GeomFromGeoJSON, ST_Contains) — NOT mapped
    here, so the ORM stays free of a geoalchemy2 dependency. This model maps only
    the scalar columns for ordinary reads.
    """
    __tablename__ = "quest_places"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    osm_id: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(20))
    area: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestPlaceSet(Base):
    """Characteristic species-set of a place × half-month window (multi-year iNat
    aggregate) — the badge TARGET (RFC-quests §3a/§13). Year-agnostic; a badge is
    a yearly instance over this set."""
    __tablename__ = "quest_place_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    place_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quest_places.id", ondelete="CASCADE"))
    window_label: Mapped[str] = mapped_column(String(40))     # 'first-half-05'
    species_set: Mapped[list | None] = mapped_column(ARRAY(Text))   # latin_keys
    # Per-species display meta [{key, latin, name, photo}], saved at compute time
    # so place/{id}/set serves cards WITHOUT a live iNat round-trip (handoff
    # quests-places). Nullable — sets computed before this column fall back to
    # corpus-resolution in the endpoint.
    species_meta: Mapped[list | None] = mapped_column(JSONB)
    target: Mapped[int | None] = mapped_column(Integer)        # round(0.6*|set|) clamp [5,15]
    obs_total: Mapped[int | None] = mapped_column(Integer)     # density signal
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestIssuedBadge(Base):
    """A server-issued yearly badge instance with an ordinal — the scarcity record
    (RFC-quests §3a/§8a). A badge has up to 3 TIERS (новичок/любитель/мастер,
    HANDOFF-gamification-tiers); each tier is its own row with its own per-tier
    ordinal scarcity, idempotent per (badge_id, device_key, tier)."""
    __tablename__ = "quest_issued_badges"
    __table_args__ = (UniqueConstraint("badge_id", "device_key", "tier", name="uq_badge_device_tier"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    badge_id: Mapped[str] = mapped_column(Text)               # '{place_id}:{window}:{year}'
    device_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tier: Mapped[int] = mapped_column(Integer, server_default="1")   # 1 новичок / 2 любитель / 3 мастер
    points: Mapped[int | None] = mapped_column(Integer)             # tier points (5/15/30), stamped at issue
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ordinal: Mapped[int | None] = mapped_column(Integer)            # per-(badge_id, tier) scarcity rank
    window_closed: Mapped[bool] = mapped_column(Boolean, server_default="false")
