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
from app.services.quests import auto_handle, set_activity_public
from app.services.profanity import is_offensive

router = APIRouter()


class NicknameUpdate(BaseModel):
    nickname: str | None = None


class AvatarUpdate(BaseModel):
    avatar: str | None = None


class PrivacyUpdate(BaseModel):
    activity_public: bool = True


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
    h = auto_handle(device_key)
    stmt = pg_insert(Device).values(device_key=device_key, handle=h)
    stmt = stmt.on_conflict_do_update(
        index_elements=["device_key"],
        set_={"last_seen": func.now(), "handle": func.coalesce(Device.handle, stmt.excluded.handle)})
    await db.execute(stmt)
    await db.commit()
    d = await db.get(Device, device_key)
    # handle = PUBLIC id for share links/profile URLs; activity_public seeds the
    # client's privacy toggle.
    return {"status": "ok", "device_key": str(device_key),
            "handle": (d.handle if d else None) or h,
            "activity_public": bool(d.activity_public) if d else True}


@router.patch("/{device_key}/nickname")
async def set_nickname(device_key: uuid.UUID, body: NicknameUpdate,
                       db: AsyncSession = Depends(get_db)):
    """Optional badge-shelf nickname (set from settings, never prompted)."""
    nick = (body.nickname or "").strip() or None
    if nick and is_offensive(nick):
        raise HTTPException(status_code=400, detail="Недопустимое имя — выбери другое.")
    # Auto-register: a rename must persist even if the device never hit /register yet
    # (e.g. went straight to Профиль) — else it 404'd silently and «имя не менялось на
    # страницах» (Oleg, 2026-06-21).
    d = await db.get(Device, device_key)
    if d:
        d.nickname = nick
    else:
        db.add(Device(device_key=device_key, nickname=nick))
    await db.commit()
    return {"status": "ok", "nickname": nick}


@router.patch("/{device_key}/avatar")
async def set_avatar(device_key: uuid.UUID, body: AvatarUpdate,
                     db: AsyncSession = Depends(get_db)):
    """Pick an avatar from the curated set (or null to clear). Validated against
    the known slugs so the stored value always resolves to an asset."""
    av = (body.avatar or "").strip() or None
    if av is not None and av not in AVATARS:
        raise HTTPException(status_code=400, detail="unknown avatar")
    d = await db.get(Device, device_key)   # auto-register (same fix as nickname)
    if d:
        d.avatar = av
    else:
        db.add(Device(device_key=device_key, avatar=av))
    await db.commit()
    return {"status": "ok", "avatar": av}


@router.patch("/{device_key}/privacy")
async def set_privacy(device_key: uuid.UUID, body: PrivacyUpdate,
                      db: AsyncSession = Depends(get_db)):
    """«Показывать мою активность» — off removes me from followers' feeds."""
    res = await set_activity_public(db, device_key, body.activity_public)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.get("/avatars")
async def list_avatars():
    """The curated avatar slugs (client renders the picker from these)."""
    return {"avatars": sorted(AVATARS)}


# ------------------------------------------------- push tokens (RFC-v2 §5)

_PUSH_DDL = """
CREATE TABLE IF NOT EXISTS push_tokens (
    device_key uuid NOT NULL,
    platform text NOT NULL,          -- 'apns' | 'rustore'
    token text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (device_key, platform)
)
"""
_push_ddl_done = False


class PushTokenUpdate(BaseModel):
    platform: str          # apns | rustore | fcm
    token: str


@router.post("/{device_key}/push-token")
async def set_push_token(device_key: uuid.UUID, body: PushTokenUpdate,
                         db: AsyncSession = Depends(get_db)):
    """Регистрация push-токена девайса (APNs / RuStore Push). Идемпотентно —
    последний токен платформы побеждает. Пустой token = отписка платформы."""
    global _push_ddl_done
    from sqlalchemy import text as sa_text
    if not _push_ddl_done:
        await db.execute(sa_text(_PUSH_DDL))
        await db.commit()
        _push_ddl_done = True
    plat = (body.platform or "").strip().lower()
    if plat not in ("apns", "rustore", "fcm"):
        raise HTTPException(status_code=422, detail="platform must be apns|rustore|fcm")
    tok = (body.token or "").strip()
    if not tok:
        await db.execute(sa_text(
            "DELETE FROM push_tokens WHERE device_key=:dk AND platform=:p"),
            {"dk": str(device_key), "p": plat})
        await db.commit()
        return {"ok": True, "unsubscribed": True}
    if len(tok) > 4096:
        raise HTTPException(status_code=422, detail="token too long")
    await db.execute(sa_text("""
        INSERT INTO push_tokens (device_key, platform, token)
        VALUES (:dk, :p, :t)
        ON CONFLICT (device_key, platform)
        DO UPDATE SET token = EXCLUDED.token, updated_at = now()"""),
        {"dk": str(device_key), "p": plat, "t": tok})
    await db.commit()
    return {"ok": True}
