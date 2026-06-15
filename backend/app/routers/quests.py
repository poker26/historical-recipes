"""Quests: the walk engine endpoint (Phase 3). Badges land here later."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import quests

router = APIRouter()


@router.get("/walk")
async def walk(lat: float = Query(...), lng: float = Query(...),
               month: int | None = Query(None, ge=1, le=12, description="phenology filter"),
               theme: str | None = Query(None, description="optional spice, e.g. 'edible' (→ non-toxic only)"),
               db: AsyncSession = Depends(get_db)):
    """«5 species nearby» for the current point — adaptive radius, recognizable
    plants, theme-safety, bridged to the corpus (plant_id null = no monograph)."""
    return await quests.build_walk(db, lat, lng, month=month, theme=theme)


@router.post("/set/compute")
async def compute_set(place_id: str = Query(...), window: str = Query(..., description="e.g. 'first-half-06'"),
                      db: AsyncSession = Depends(get_db)):
    """Precompute a place×window species-set (the badge target). Admin/backfill;
    a Temporal workflow will fan this over known places×windows later."""
    return await quests.compute_species_set(db, place_id, window)


@router.get("/badge/progress")
async def badge_progress(device_key: str = Query(...), place_id: str = Query(...),
                         window: str = Query(...), year: int = Query(...),
                         db: AsyncSession = Depends(get_db)):
    return await quests.badge_progress(db, device_key, place_id, window, year)


@router.post("/badge/claim")
async def badge_claim(device_key: str = Query(...), place_id: str = Query(...),
                      window: str = Query(...), year: int = Query(...),
                      db: AsyncSession = Depends(get_db)):
    return await quests.claim_badge(db, device_key, place_id, window, year)


@router.get("/badges")
async def badges(device_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    return {"device_key": device_key, "badges": await quests.badge_shelf(db, device_key)}
