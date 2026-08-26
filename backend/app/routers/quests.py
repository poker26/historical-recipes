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


@router.get("/nearby")
async def nearby(lat: float = Query(...), lng: float = Query(...),
                 biotope: str | None = Query(None, description="override the point's OSM biotope"),
                 month: int | None = Query(None, ge=1, le=12, description="phenology filter"),
                 limit: int = Query(15, ge=1, le=40, description="how many ranked species to return"),
                 device_key: str | None = Query(None, description="enables per-item found-state + biotope_progress"),
                 db: AsyncSession = Depends(get_db)):
    """«Растения рядом» — cold-start base (RFC-cold-start-nearby): live iNat near the
    point, ranked so habitat-appropriate corpus species lead. Works anywhere. Returns
    up to `limit` + `has_more` so the client pages «другие» locally (Q2). With
    `device_key`: each item carries `found` (this device already identified it) +
    `found_count`, and `biotope_progress` for the point's biotope (RFC-biotope-mastery)."""
    return await quests.nearby(db, lat, lng, biotope=biotope, month=month, limit=limit,
                               device_key=device_key)


@router.get("/biotope-progress")
async def biotope_progress(device_key: str = Query(...),
                           biotope: str = Query(..., description="canonical biotope key, e.g. 'лес'"),
                           db: AsyncSession = Depends(get_db)):
    """«Знаток <биотопа>» progress for this device (RFC-biotope-mastery §B): distinct
    characteristic species found ON LOCATION in the biotope, on the richness-tuned tier
    ladder. Same shape as place `badge/progress`."""
    return await quests.biotope_progress(db, device_key, biotope)


@router.post("/biotope/claim")
async def biotope_claim(device_key: str = Query(...), biotope: str = Query(...),
                        db: AsyncSession = Depends(get_db)):
    """Claim earned «Знаток <биотопа>» tiers (silent UUID award, cumulative — no window)."""
    return await quests.claim_biotope_badge(db, device_key, biotope)


@router.post("/set/compute")
async def compute_set(place_id: str = Query(...), window: str = Query(..., description="e.g. 'first-half-06'"),
                      force: bool = Query(False, description="bypass the density floor (test places in sparse areas)"),
                      db: AsyncSession = Depends(get_db)):
    """Precompute a place×window species-set (the badge target). Admin/backfill;
    a Temporal workflow will fan this over known places×windows later. `force` skips
    the _MIN_OBS density gate for test places."""
    return await quests.compute_species_set(db, place_id, window, force=force)


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


@router.get("/claimable")
async def claimable(device_key: str = Query(...), biotopes: bool = Query(True),
                    db: AsyncSession = Depends(get_db)):
    """Заработанные, но НЕ забранные значки девайса за текущее окно (места + биотопы)
    одним вызовом — для баннера «забери значок» на главной / экране квестов."""
    return await quests.claimable_badges(db, device_key, include_biotopes=biotopes)


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


@router.post("/custom/estimate")
async def custom_estimate(lat: float = Query(...), lng: float = Query(...),
                          radius_km: float = Query(1.0),
                          movement_mode: str = Query("walk"),
                          month: int | None = Query(None, ge=1, le=12),
                          db: AsyncSession = Depends(get_db)):
    """Дешёвая оценка области ДО создания кастомной прогулки: жизнеспособность,
    счётчики видов, биотопы, минимальный жизнеспособный радиус (RFC-v2 §7.3)."""
    return await quests.estimate_custom_area(db, lat, lng, radius_km, movement_mode, month)


@router.post("/custom/create")
async def custom_create(lat: float = Query(...), lng: float = Query(...),
                        radius_km: float = Query(1.0, ge=0.3, le=2.5),
                        window: str | None = Query(None, description="default = current half-month"),
                        device_key: str | None = Query(None),
                        db: AsyncSession = Depends(get_db)):
    """«Закажи свой квест» (RFC-custom-quests, premium): build a circle quest around the
    point (biotope-themed) and compute its species-set — iNat ∪ GBIF inside the circle,
    plus biotope×region «expected» species when local point-data is sparse. Returns the
    place so the client opens it like any other quest. (Premium gating is a later phase.)"""
    return await quests.create_custom_quest(db, lat, lng, radius_km=radius_km, window=window)


@router.get("/places/in-bounds")
async def places_in_bounds(min_lat: float = Query(...), min_lng: float = Query(...),
                           max_lat: float = Query(...), max_lng: float = Query(...),
                           window: str | None = Query(None, description="default = current half-month"),
                           limit: int = Query(300, ge=1, le=1000),
                           device_key: str | None = Query(None, description="mark this device's passed/started status"),
                           db: AsyncSession = Depends(get_db)):
    """Quest places inside the map viewport (pan-to-search). Pins only, DB-only —
    lets the client refetch markers as the user scrolls the map anywhere. With
    `device_key`, each place also carries `top_tier`/`started` so the list can show
    «✓ пройден» / «в процессе»."""
    return await quests.places_in_bounds(db, min_lat, min_lng, max_lat, max_lng,
                                         window=window, limit=limit, device_key=device_key)


@router.get("/place/{place_id}/set")
async def place_set(place_id: str, window: str | None = Query(None),
                    device_key: str | None = Query(None),
                    biotope: str | None = Query(None, description="filter to species of this habitat (GPS→biotope)"),
                    db: AsyncSession = Depends(get_db)):
    """«What to look for here» — species cards from the saved set (no live iNat).
    `biotope` filters to species of that habitat."""
    return await quests.place_set(db, place_id, window=window, device_key=device_key,
                                  biotope=biotope)


@router.get("/place/{place_id}/participants")
async def place_participants(place_id: str, window: str | None = Query(None),
                             year: int | None = Query(None),
                             db: AsyncSession = Depends(get_db)):
    """«Кто проходил этот квест» — people who earned a badge for this place×window, to
    befriend those who did the same quest (vs the global leaderboard). Public only."""
    return await quests.place_participants(db, place_id, window=window, year=year)


@router.get("/place/{place_id}/biotopes")
async def place_biotopes(place_id: str, window: str | None = Query(None),
                         db: AsyncSession = Depends(get_db)):
    """«Что искать здесь, по среде» — the place's landcover biotopes (GPS→biotope)
    with the count of its expected species in each. Tap → `set?biotope=<key>`."""
    return await quests.place_biotopes(db, place_id, window=window)


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

@router.get("/profile/{subject}")
async def public_profile(subject: str, viewer_device_key: str | None = Query(None),
                         db: AsyncSession = Depends(get_db)):
    """Public «паспорт натуралиста» by `handle` (or legacy device_key): nick, avatar,
    level, score, rank + badge shelf (place names, no coordinates). `is_following` set
    when `viewer_device_key` is given. 404 for unknown handle / blocked."""
    p = await quests.public_profile(db, subject, viewer_device_key=viewer_device_key)
    if p is None:
        raise HTTPException(status_code=404, detail="not found")
    return p


@router.get("/recent-badges")
async def recent_badges(limit: int = Query(20, ge=1, le=100),
                        place_id: str | None = Query(None),
                        db: AsyncSession = Depends(get_db)):
    """Newest issued badges — social-proof activity feed (nick + place + tier)."""
    return await quests.recent_badges(db, limit=limit, place_id=place_id)


# --------- Follows + activity feed (Phase 8). follower = private device_key. ---------

@router.post("/follow")
async def follow(device_key: str = Query(...), target: str = Query(..., description="followee handle"),
                 db: AsyncSession = Depends(get_db)):
    res = await quests.follow(db, device_key, target)
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res


@router.post("/unfollow")
async def unfollow(device_key: str = Query(...), target: str = Query(...),
                   db: AsyncSession = Depends(get_db)):
    return await quests.unfollow(db, device_key, target)


@router.get("/following")
async def following(device_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    return await quests.following(db, device_key)


@router.get("/meta")
async def meta(device_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Награды без географии: «Собиратель» (сколько разных видов) и «Постоянство»
    (лучшая серия дней подряд). Работают у всех, включая тех, кто никогда не
    оказывался внутри квест-места — а это 92% устройств с геолокацией."""
    return await quests.meta_progress(db, device_key)


@router.post("/meta/{kind}/claim")
async def claim_meta(kind: str, device_key: str = Query(...),
                     db: AsyncSession = Depends(get_db)):
    res = await quests.claim_meta_badge(db, device_key, kind)
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


@router.get("/feed")
async def feed(device_key: str | None = Query(None), limit: int = Query(30, ge=1, le=100),
               scope: str = Query("following", pattern="^(following|ether)$"),
               db: AsyncSession = Depends(get_db)):
    """Merged recent activity (in-corpus finds + badges). scope=following — подписки;
    scope=ether — «Эфир»: все публичные находки сообщества, место приблизительное
    (имя известного места), координат нет никогда.

    Эфиру device_key не нужен: он собирает всех, кто оставил активность публичной,
    и ничего не персонализирует. Поэтому лендинг botanik.fun читает ту же ленту
    анонимно. Ленте подписок ключ обязателен — без него не от кого считать подписки."""
    if scope == "following" and not device_key:
        raise HTTPException(400, "device_key required for scope=following")
    return await quests.feed(db, device_key, limit=limit, scope=scope)
