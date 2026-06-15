"""Quest places: OSM named-place ingest + point→place lookup (quests Phase 2)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import osm

router = APIRouter()


@router.get("/at")
async def place_at(lat: float = Query(...), lng: float = Query(...),
                   db: AsyncSession = Depends(get_db)):
    """Named places covering this GPS point, most specific (smallest) first.
    Empty → not inside any named place (walk-only, no badge — RFC-quests §13)."""
    places = await osm.point_to_place(db, lat, lng)
    return {"lat": lat, "lng": lng, "places": places,
            "most_specific": places[0] if places else None}


@router.post("/ingest")
async def ingest(s: float = Query(..., description="south lat"),
                 w: float = Query(..., description="west lng"),
                 n: float = Query(..., description="north lat"),
                 e: float = Query(..., description="east lng"),
                 db: AsyncSession = Depends(get_db)):
    """Pull named OSM places inside a bbox into quest_places (idempotent). Admin/
    backfill trigger; for a region, call per tile. Heavy → run sparingly."""
    return await osm.ingest_bbox(db, s, w, n, e)


@router.get("/count")
async def count(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(text(
        "SELECT kind, count(*) FROM quest_places GROUP BY kind ORDER BY count(*) DESC"))).all()
    total = (await db.execute(text("SELECT count(*) FROM quest_places"))).scalar()
    return {"total": total, "by_kind": {r[0]: r[1] for r in rows}}
