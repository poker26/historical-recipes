import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlantReaderMonograph(Base):
    """The precomputed, grounded, review-gated «reader monograph» for ONE plant
    (RFC-reader-monograph, Layer 2). `?view=field` serves THIS when present+reviewed;
    otherwise it falls back to the deterministic on-the-fly aggregation.

    Two-tier by design: the MCP `get_plant` / default view stay RAW (agents need the
    full source-grounded facts); humans in the field get this vetted, capped, prose
    monograph. Idempotent regen: `generated_from_hash` = sha256 of the distilled
    input facts → a batch only regenerates plants whose distilled input changed.
    """

    __tablename__ = "plant_reader_monograph"

    plant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plants.id", ondelete="CASCADE"), primary_key=True)

    monograph: Mapped[dict] = mapped_column(JSONB)            # the §4 contract JSON
    generated_from_hash: Mapped[str] = mapped_column(String(80))  # sha256:… of distilled input
    model: Mapped[str | None] = mapped_column(String(60))
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # publish-gate outcome cached for audit (P0 blockers + P1 warnings, [] if clean)
    gate_findings: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
