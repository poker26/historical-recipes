"""Silent device identity for quests (RFC-quests §8a, PLAN-quests-backend §1).

No accounts, no PII: the client generates a random UUID and registers it once.
Re-registration is idempotent (bumps last_seen). Nickname is optional and only
set from settings — never prompted.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device

router = APIRouter()


class NicknameUpdate(BaseModel):
    nickname: str | None = None


class AvatarUpdate(BaseModel):
    avatar: str | None = None


# Curated avatar set (slugs) — assets live at botanik.fun/avatars/{slug}.png.
AVATARS = {
    "cactus", "sunflower", "flyagaric", "dandelion", "monstera", "aloe", "basil",
    "rose", "fern", "oak", "clover", "tulip", "wheat", "flytrap", "succulent", "pine",
}


@router.post("/register")
async def register_device(device_key: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    """Silently upsert the client-generated device UUID. Idempotent — a repeat
    call just refreshes last_seen. The only thing the client must do for quests.
    `device_key` is a QUERY param — consistent with every other /api/quests route
    (and with how the «Что растёт» client calls it)."""
    stmt = pg_insert(Device).values(device_key=device_key).on_conflict_do_update(
        index_elements=["device_key"], set_={"last_seen": func.now()})
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok", "device_key": str(device_key)}


@router.patch("/{device_key}/nickname")
async def set_nickname(device_key: uuid.UUID, body: NicknameUpdate,
                       db: AsyncSession = Depends(get_db)):
    """Optional badge-shelf nickname (set from settings, never prompted)."""
    d = await db.get(Device, device_key)
    if not d:
        raise HTTPException(status_code=404, detail="device not registered")
    d.nickname = (body.nickname or "").strip() or None
    await db.commit()
    return {"status": "ok", "nickname": d.nickname}


@router.patch("/{device_key}/avatar")
async def set_avatar(device_key: uuid.UUID, body: AvatarUpdate,
                     db: AsyncSession = Depends(get_db)):
    """Pick an avatar from the curated set (or null to clear). Validated against
    the known slugs so the stored value always resolves to an asset."""
    d = await db.get(Device, device_key)
    if not d:
        raise HTTPException(status_code=404, detail="device not registered")
    av = (body.avatar or "").strip() or None
    if av is not None and av not in AVATARS:
        raise HTTPException(status_code=400, detail="unknown avatar")
    d.avatar = av
    await db.commit()
    return {"status": "ok", "avatar": d.avatar}


@router.get("/avatars")
async def list_avatars():
    """The curated avatar slugs (client renders the picker from these)."""
    return {"avatars": sorted(AVATARS)}
