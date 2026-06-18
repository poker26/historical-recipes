"""Quests: the walk engine endpoint (Phase 3). Badges land here later."""
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("/places/near")
async def places_near(lat: float = Query(...), lng: float = Query(...),
                      device_key: str | None = Query(None),
                      radius_km: float = Query(25, gt=0, le=200),
                      limit: int = Query(20, ge=1, le=100),
                      window: str | None = Query(None, description="default = current half-month"),
                      db: AsyncSession = Depends(get_db)):
    """Nearby places that have a precomputed species-set for the window — for the
    map/list. Instant (DB only, no live iNat)."""
    return await quests.places_near(db, lat, lng, device_key=device_key,
                                    radius_km=radius_km, limit=limit, window=window)


@router.get("/place/{place_id}/set")
async def place_set(place_id: str, window: str | None = Query(None),
                    device_key: str | None = Query(None),
                    db: AsyncSession = Depends(get_db)):
    """«What to look for here» — species cards from the saved set (no live iNat)."""
    return await quests.place_set(db, place_id, window=window, device_key=device_key)


@router.get("/leaderboard")
async def leaderboard(device_key: str | None = Query(None),
                      limit: int = Query(20, ge=1, le=100),
                      scope: str = Query("global", pattern="^(global|place|season)$"),
                      place_id: str | None = Query(None, description="for scope=place"),
                      window: str | None = Query(None, description="for scope=season, e.g. 'second-half-06'"),
                      year: int | None = Query(None, description="for scope=season; default current year"),
                      db: AsyncSession = Depends(get_db)):
    """All-time leaderboard. Score = Σ highest-tier points per place×season badge
    (5/15/30), server-counted. `scope`: global (default) · place (`place_id`) ·
    season (`window`×`year`)."""
    return await quests.leaderboard(db, device_key=device_key, limit=limit,
                                    scope=scope, place_id=place_id, window=window, year=year)


# --------- Public read surfaces for the landing (botanik.fun). No coordinates. ---------

@router.get("/profile/{device_key}")
async def public_profile(device_key: str, db: AsyncSession = Depends(get_db)):
    """Public «паспорт натуралиста»: nick, level, score, rank + badge shelf with
    place names. 404 on a malformed key; a valid-but-unknown key returns an empty
    (but shareable) profile."""
    try:
        _uuid.UUID(device_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    return await quests.public_profile(db, device_key)


@router.get("/recent-badges")
async def recent_badges(limit: int = Query(20, ge=1, le=100),
                        place_id: str | None = Query(None),
                        db: AsyncSession = Depends(get_db)):
    """Newest issued badges — social-proof activity feed (nick + place + tier)."""
    return await quests.recent_badges(db, limit=limit, place_id=place_id)
