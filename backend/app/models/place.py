import uuid
from datetime import datetime

from sqlalchemy import String, Text, Float, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
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
    target: Mapped[int | None] = mapped_column(Integer)        # round(0.6*|set|) clamp [5,15]
    obs_total: Mapped[int | None] = mapped_column(Integer)     # density signal
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestIssuedBadge(Base):
    """A server-issued yearly badge instance with an ordinal — the scarcity record
    (RFC-quests §3a/§8a)."""
    __tablename__ = "quest_issued_badges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    badge_id: Mapped[str] = mapped_column(Text)               # '{place_id}:{window}:{year}'
    device_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ordinal: Mapped[int | None] = mapped_column(Integer)
    window_closed: Mapped[bool] = mapped_column(Boolean, server_default="false")
