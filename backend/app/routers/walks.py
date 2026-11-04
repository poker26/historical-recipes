"""Сессии прогулок (RFC-v2 §7.2): «позвать друга по ссылке» — единственная социальная
механика v2.0. Сессия (walk_instance) = «когда гуляли и с кем»; она живёт ПОВЕРХ
кумулятивного мастерства места и никогда его не сбрасывает.

Правила:
- прогулка привязана к quest_place (подготовленное место ИЛИ кастомный круг);
- ссылка botanik.fun/walk/{id} несёт только walk_instance_id — ни device_key, ни координат;
- личная находка всегда в коллекции автора; сессия только агрегирует вклад;
- все операции идемпотентны.

Таблицы создаются лениво (паттерн leads/product_events; прод-alembic диверджнут).
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import quests as quests_svc
from app.services.plant_matching import synonym_map
from app.services.quests import _credit_keys, _pool_for, auto_handle, auto_nick

logger = logging.getLogger(__name__)
router = APIRouter()

_DDL = """
CREATE TABLE IF NOT EXISTS walk_instances (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL DEFAULT 'prepared',        -- prepared | custom
    quest_place_id uuid NOT NULL,
    owner_device_key uuid NOT NULL,
    planned_at timestamptz,
    started_at timestamptz,
    ended_at timestamptz,
    status text NOT NULL DEFAULT 'draft',         -- draft | planned | active | done | cancelled
    visibility text NOT NULL DEFAULT 'link',      -- link (по ссылке) | private
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS walk_participants (
    walk_instance_id uuid NOT NULL,
    device_key uuid NOT NULL,
    role text NOT NULL DEFAULT 'member',          -- owner | member
    joined_at timestamptz NOT NULL DEFAULT now(),
    left_at timestamptz,
    status text NOT NULL DEFAULT 'joined',        -- joined | left
    PRIMARY KEY (walk_instance_id, device_key)
);
CREATE INDEX IF NOT EXISTS walk_participants_device ON walk_participants (device_key)
"""
_ddl_done = False


async def _ensure(db: AsyncSession) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for stmt in _DDL.strip().split(";"):
        if stmt.strip():
            await db.execute(text(stmt))
    await db.commit()
    _ddl_done = True


def _uuid_or_422(raw: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(raw).strip()))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"bad {what}")


async def _walk_or_404(db, walk_id: str) -> dict:
    row = (await db.execute(text("""
        SELECT w.id::text, w.kind, w.quest_place_id::text, w.owner_device_key::text,
               w.planned_at, w.started_at, w.ended_at, w.status, w.visibility,
               p.name AS place_name
        FROM walk_instances w LEFT JOIN quest_places p ON p.id = w.quest_place_id
        WHERE w.id = CAST(:w AS uuid)"""), {"w": walk_id})).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="walk not found")
    return dict(row)


async def _participants(db, walk_id: str) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT wp.device_key::text, wp.role, wp.joined_at,
               d.handle, d.nickname, d.avatar
        FROM walk_participants wp
        LEFT JOIN quest_devices d ON d.device_key = wp.device_key
        WHERE wp.walk_instance_id = CAST(:w AS uuid) AND wp.status = 'joined'
        ORDER BY wp.joined_at"""), {"w": walk_id})).all()
    return [{
        "handle": h or auto_handle(dk), "nick": n or auto_nick(dk),
        "avatar": av, "role": role,
    } for dk, role, _j, h, n, av in rows]


@router.post("")
async def create_walk(device_key: str = Query(...), place_id: str = Query(...),
                      kind: str = Query("prepared"),
                      planned_at: str | None = Query(None),
                      db: AsyncSession = Depends(get_db)):
    """Создать прогулку-сессию в месте (подготовленном или кастомном). Владелец
    автоматически становится участником. Возвращает id → ссылка /walk/{id}."""
    await _ensure(db)
    dk = _uuid_or_422(device_key, "device_key")
    pid = _uuid_or_422(place_id, "place_id")
    if kind not in ("prepared", "custom"):
        raise HTTPException(status_code=422, detail="kind must be prepared|custom")
    exists = (await db.execute(text(
        "SELECT 1 FROM quest_places WHERE id = CAST(:p AS uuid)"), {"p": pid})).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="place not found")
    ts = None
    if planned_at:
        try:
            ts = datetime.fromisoformat(planned_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="bad planned_at")
    wid = (await db.execute(text("""
        INSERT INTO walk_instances (kind, quest_place_id, owner_device_key, planned_at, status)
        VALUES (:k, CAST(:p AS uuid), CAST(:d AS uuid), :ts,
                CASE WHEN CAST(:ts AS timestamptz) IS NULL THEN 'draft' ELSE 'planned' END)
        RETURNING id::text"""), {"k": kind, "p": pid, "d": dk, "ts": ts})).scalar()
    await db.execute(text("""
        INSERT INTO walk_participants (walk_instance_id, device_key, role)
        VALUES (CAST(:w AS uuid), CAST(:d AS uuid), 'owner')
        ON CONFLICT DO NOTHING"""), {"w": wid, "d": dk})
    await db.commit()
    return {"id": wid, "share_path": f"/walk/{wid}", "status": "planned" if ts else "draft"}


@router.get("/{walk_id}")
async def get_walk(walk_id: str, db: AsyncSession = Depends(get_db)):
    """Публичный предпросмотр по ссылке: место, статус, время, участники (handle/ник/
    аватар) — БЕЗ device_key и координат."""
    await _ensure(db)
    w = await _walk_or_404(db, _uuid_or_422(walk_id, "walk_id"))
    parts = await _participants(db, w["id"])
    return {
        "id": w["id"], "kind": w["kind"], "status": w["status"],
        "place": {"id": w["quest_place_id"], "name": w["place_name"]},
        "planned_at": w["planned_at"].isoformat() if w["planned_at"] else None,
        "started_at": w["started_at"].isoformat() if w["started_at"] else None,
        "ended_at": w["ended_at"].isoformat() if w["ended_at"] else None,
        "participants": parts, "participant_count": len(parts),
    }


@router.post("/{walk_id}/join")
async def join_walk(walk_id: str, device_key: str = Query(...),
                    db: AsyncSession = Depends(get_db)):
    """Присоединиться по ссылке. Идемпотентно; выходивший участник возвращается."""
    await _ensure(db)
    wid = _uuid_or_422(walk_id, "walk_id")
    dk = _uuid_or_422(device_key, "device_key")
    w = await _walk_or_404(db, wid)
    if w["status"] in ("done", "cancelled"):
        raise HTTPException(status_code=409, detail="walk finished")
    await db.execute(text("""
        INSERT INTO walk_participants (walk_instance_id, device_key, role)
        VALUES (CAST(:w AS uuid), CAST(:d AS uuid), 'member')
        ON CONFLICT (walk_instance_id, device_key)
        DO UPDATE SET status = 'joined', left_at = NULL"""), {"w": wid, "d": dk})
    await db.commit()
    return {"ok": True, "walk": await get_walk(wid, db)}


@router.post("/{walk_id}/leave")
async def leave_walk(walk_id: str, device_key: str = Query(...),
                     db: AsyncSession = Depends(get_db)):
    await _ensure(db)
    wid = _uuid_or_422(walk_id, "walk_id")
    dk = _uuid_or_422(device_key, "device_key")
    await db.execute(text("""
        UPDATE walk_participants SET status = 'left', left_at = now()
        WHERE walk_instance_id = CAST(:w AS uuid) AND device_key = CAST(:d AS uuid)"""),
        {"w": wid, "d": dk})
    await db.commit()
    return {"ok": True}


async def _owner_transition(db, walk_id: str, device_key: str, action: str) -> dict:
    wid = _uuid_or_422(walk_id, "walk_id")
    dk = _uuid_or_422(device_key, "device_key")
    w = await _walk_or_404(db, wid)
    if w["owner_device_key"] != dk:
        raise HTTPException(status_code=403, detail="owner only")
    if action == "start":
        if w["status"] == "active":
            return {"ok": True, "status": "active"}          # идемпотентно
        await db.execute(text("""
            UPDATE walk_instances SET status = 'active',
                   started_at = COALESCE(started_at, now())
            WHERE id = CAST(:w AS uuid)"""), {"w": wid})
    else:
        if w["status"] == "done":
            return {"ok": True, "status": "done"}
        await db.execute(text("""
            UPDATE walk_instances SET status = 'done', ended_at = COALESCE(ended_at, now())
            WHERE id = CAST(:w AS uuid)"""), {"w": wid})
    await db.commit()
    return {"ok": True, "status": "active" if action == "start" else "done"}


@router.post("/{walk_id}/start")
async def start_walk(walk_id: str, device_key: str = Query(...),
                     db: AsyncSession = Depends(get_db)):
    await _ensure(db)
    return await _owner_transition(db, walk_id, device_key, "start")


@router.post("/{walk_id}/finish")
async def finish_walk(walk_id: str, device_key: str = Query(...),
                      db: AsyncSession = Depends(get_db)):
    await _ensure(db)
    return await _owner_transition(db, walk_id, device_key, "finish")


@router.get("/{walk_id}/progress")
async def walk_progress(walk_id: str, db: AsyncSession = Depends(get_db)):
    """Вклад участников: distinct-виды из пула места, найденные КАЖДЫМ внутри полигона
    за время сессии (started_at..ended_at|now). Групповой счёт = union по участникам.
    Личная коллекция не затрагивается — сессия только читает identifications."""
    await _ensure(db)
    wid = _uuid_or_422(walk_id, "walk_id")
    w = await _walk_or_404(db, wid)
    if not w["started_at"]:
        parts = await _participants(db, wid)
        return {"id": wid, "status": w["status"], "started": False,
                "participants": [{**p, "found": 0, "species": []} for p in parts],
                "group_found": 0, "pool_size": None}
    pool, target = await _pool_for(db, w["quest_place_id"])
    syn = await synonym_map(db)
    prow = (await db.execute(text("""
        SELECT wp.device_key::text, d.handle, d.nickname, d.avatar, wp.role
        FROM walk_participants wp LEFT JOIN quest_devices d ON d.device_key = wp.device_key
        WHERE wp.walk_instance_id = CAST(:w AS uuid) AND wp.status = 'joined'
        ORDER BY wp.joined_at"""), {"w": wid})).all()
    end = w["ended_at"] or datetime.now(timezone.utc)
    out: list[dict] = []
    group: set = set()
    for dk, h, n, av, role in prow:
        rows = (await db.execute(text("""
            SELECT candidates, top_latin, top_score FROM identifications
            WHERE device_key = CAST(:dk AS uuid) AND lat IS NOT NULL
              AND captured_at >= :f AND captured_at <= :t
              AND ST_Contains((SELECT geom FROM quest_places WHERE id = CAST(:p AS uuid)),
                              ST_SetSRID(ST_MakePoint(lng, lat), 4326))"""),
            {"dk": dk, "f": w["started_at"], "t": end, "p": w["quest_place_id"]})).all()
        found: set = set()
        for cands, tl, ts_ in rows:
            ex, sf = _credit_keys(cands, tl, ts_, pool, syn)
            found |= ex | sf
        group |= found
        out.append({"handle": h or auto_handle(dk), "nick": n or auto_nick(dk),
                    "avatar": av, "role": role,
                    "found": len(found), "species": sorted(found)})
    return {"id": wid, "status": w["status"], "started": True,
            "place": {"id": w["quest_place_id"], "name": w["place_name"]},
            "participants": out, "group_found": len(group),
            "group_species": sorted(group), "pool_size": len(pool), "target": target}
