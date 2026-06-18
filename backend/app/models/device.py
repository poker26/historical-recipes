import uuid
from datetime import datetime

from sqlalchemy import String, Text, Boolean, DateTime, func
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
    # Chosen avatar slug from the curated set (e.g. "cactus"); null = default.
    avatar: Mapped[str | None] = mapped_column(String(40))
    # PUBLIC handle (short slug) — the only id shown on public surfaces (profile URL,
    # leaderboard, feed, follow targets). device_key stays private (write token).
    handle: Mapped[str | None] = mapped_column(Text, unique=True)
    # «Показывать мою активность» — off = excluded from followers' feeds.
    activity_public: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # Moderation: hidden from all public surfaces when true.
    blocked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestFollow(Base):
    """Follow edge for the activity feed. follower = device_key (private «me»);
    followee = handle (public target). Layer user_id on later without changing this."""
    __tablename__ = "quest_follows"

    follower_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    followee_handle: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
