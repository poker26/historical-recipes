"""Admin-only nickname moderation. Mounted at /api/moderation — NOT in the public
flora nginx whitelist, so it's reachable only via the mTLS-gated admin domain.
Blocking sets quest_devices.blocked, which P1 already excludes from leaderboard /
public profile / feed / follow targets."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.services.quests import resolve_subject

router = APIRouter()


class BlockUpdate(BaseModel):
    target: str            # handle or device_key
    blocked: bool = True


@router.get("/nicknames")
async def list_nicknames(limit: int = Query(200, ge=1, le=1000),
                         only_custom: bool = Query(True),
                         db: AsyncSession = Depends(get_db)):
    """Review queue: devices with a custom nickname (or all), newest-seen first."""
    q = select(Device.device_key, Device.handle, Device.nickname, Device.blocked, Device.last_seen)
    if only_custom:
        q = q.where(Device.nickname.isnot(None))
    rows = (await db.execute(q.order_by(desc(Device.last_seen)).limit(limit))).all()
    return {"devices": [
        {"device_key": str(dk), "handle": h, "nickname": nk, "blocked": bool(bl),
         "last_seen": ls.isoformat() if ls else None}
        for dk, h, nk, bl, ls in rows
    ]}


@router.post("/block")
async def set_blocked(body: BlockUpdate, db: AsyncSession = Depends(get_db)):
    """Block/unblock a device (by handle or device_key)."""
    dk = await resolve_subject(db, body.target)
    d = await db.get(Device, dk) if dk else None
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    d.blocked = bool(body.blocked)
    await db.commit()
    return {"status": "ok", "device_key": str(dk), "blocked": d.blocked}
