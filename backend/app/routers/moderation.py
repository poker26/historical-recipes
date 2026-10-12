"""Admin-only nickname moderation. Mounted at /api/moderation — NOT in the public
flora nginx whitelist, so it's reachable only via the mTLS-gated admin domain.
Blocking sets quest_devices.blocked, which P1 already excludes from leaderboard /
public profile / feed / follow targets."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy import text as sa_text
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


# ---- жалобы на фото в общих квестах (docs/RFC-collective-quests.md) ----

@router.get("/collective-reports")
async def collective_reports(only_open: bool = Query(True), db: AsyncSession = Depends(get_db)):
    """Фото «Общего леса», на которые пожаловались. Фото скрыто с первой жалобы."""
    rows = (await db.execute(sa_text(f"""
        SELECT s.id, s.name_ru, s.photo_hidden, s.closed_by, count(r.reporter) AS reports,
               max(r.created_at) AS last_at, bool_and(r.reviewed) AS reviewed
        FROM collective_reports r JOIN collective_slots s ON s.id = r.slot_id
        GROUP BY s.id, s.name_ru, s.photo_hidden, s.closed_by
        {"HAVING NOT bool_and(r.reviewed)" if only_open else ""}
        ORDER BY last_at DESC LIMIT 200"""))).all()
    return {"reports": [
        {"slot_id": str(sid), "name": name, "hidden": hidden, "author": str(author) if author else None,
         "reports": n, "last_at": at.isoformat(), "reviewed": reviewed,
         "photo": f"/api/moderation/collective-photo/{sid}.jpg"}
        for sid, name, hidden, author, n, at, reviewed in rows]}


@router.get("/collective-photo/{slot_id}.jpg")
async def collective_photo_admin(slot_id: str, db: AsyncSession = Depends(get_db)):
    """Скрытое фото для проверки жалобы: модерации нужно его увидеть."""
    from fastapi.responses import Response
    from app.config import settings
    from app.services import minio as minio_svc
    key = (await db.execute(sa_text("""
        SELECT i.photo_key FROM collective_slots s JOIN identifications i ON i.id = s.identification_id
        WHERE s.id = CAST(:s AS uuid)"""), {"s": slot_id})).scalar()
    if not key:
        raise HTTPException(status_code=404)
    raw = minio_svc.get_client().get_object(settings.minio_field_bucket, key)
    try:
        return Response(raw.read(), media_type="image/jpeg")
    finally:
        raw.close()
        raw.release_conn()


class CollectivePhotoUpdate(BaseModel):
    slot_id: str
    hidden: bool


@router.post("/collective-photo")
async def set_collective_photo(body: CollectivePhotoUpdate, db: AsyncSession = Depends(get_db)):
    """Решение по жалобе: hidden=false возвращает фото, hidden=true оставляет скрытым.
    В обоих случаях жалобы помечаются проверенными."""
    await db.execute(sa_text("UPDATE collective_slots SET photo_hidden = :h WHERE id = CAST(:s AS uuid)"),
                     {"h": body.hidden, "s": body.slot_id})
    await db.execute(sa_text("UPDATE collective_reports SET reviewed = true WHERE slot_id = CAST(:s AS uuid)"),
                     {"s": body.slot_id})
    await db.commit()
    return {"status": "ok", "slot_id": body.slot_id, "hidden": body.hidden}
