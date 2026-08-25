import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Identification(Base):
    """One photo-identification event from the field client, archived for
    debugging and future training data.

    Each row points at the uploaded photo in the ``field-uploads`` MinIO bucket
    (``photo_key``) and captures the context an LLM-only consumer can't see: the
    photo's own EXIF, the user-granted geolocation, and the phone/OS the shot came
    from. The engine result is stored too (``top_*`` + full ``candidates``) so a
    bad identification can be replayed against the exact inputs.

    This is internal capture — NOT exposed over MCP and NOT the per-device "my
    history" list (that lives client-side). Geolocation is only present when the
    user granted the permission; all metadata fields are nullable by design."""

    __tablename__ = "identifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Silent device identity (RFC-quests §8a): attributes this identification to a
    # device so quest-badge progress is server-verifiable from History. Nullable —
    # older rows and non-quest identifications carry none.
    device_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    photo_key: Mapped[str | None] = mapped_column(Text)          # MinIO object key in the field bucket
    engine: Mapped[str | None] = mapped_column(String(30))       # identification engine, e.g. "plantnet"
    organ: Mapped[str | None] = mapped_column(String(20))        # leaf/flower/fruit/bark/auto (first organ)

    # Geolocation (only when the user granted the permission).
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    geo_accuracy: Mapped[float | None] = mapped_column(Float)    # metres
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # EXIF/client capture time

    # Photo's own attributes (EXIF) + device/OS, for debugging.
    exif: Mapped[dict | None] = mapped_column(JSONB)
    device_model: Mapped[str | None] = mapped_column(String(120))
    device_manufacturer: Mapped[str | None] = mapped_column(String(120))
    os_version: Mapped[str | None] = mapped_column(String(40))
    os_sdk: Mapped[int | None] = mapped_column(Integer)
    app_version: Mapped[str | None] = mapped_column(String(40))

    # Engine outcome.
    top_latin: Mapped[str | None] = mapped_column(Text)
    top_score: Mapped[float | None] = mapped_column(Float)
    matched_plant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plants.id", ondelete="SET NULL"))
    matched_count: Mapped[int | None] = mapped_column(Integer)
    remaining_requests: Mapped[int | None] = mapped_column(Integer)
    candidates: Mapped[list | None] = mapped_column(JSONB)       # full candidate list as returned

    # Почему определение не состоялось (миграция 021). Без этого «кандидатов нет»
    # неотличимо от «движок вернул ошибку»: 7.1% снимков уходили в архив с пустым
    # candidates и без единого признака причины.
    #   engine_error  — движок ответил ошибкой (квота, таймаут, транспорт)
    #   no_candidates — движок ответил, но список пуст
    failure_reason: Mapped[str | None] = mapped_column(String(32), index=True)
    failure_detail: Mapped[str | None] = mapped_column(Text)
