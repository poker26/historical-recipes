import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Device(Base):
    """A silent, account-less device identity for quests (RFC-quests §8a).

    The field client generates a random UUID at first launch (kept in iOS
    Keychain / Android Block Store so it survives reinstall + migrates on
    device restore) and registers it once — no email/SMS/screen, no PII. All
    quest badges are keyed to this device_key; the nickname is optional and only
    ever set by the user in settings, never prompted.
    """
    # `quest_devices` (a pre-existing unrelated `devices` table holds hardware
    # telemetry — vendor/model — so we don't collide with it).
    __tablename__ = "quest_devices"

    device_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
