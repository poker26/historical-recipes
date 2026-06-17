"""Walk engine — "5 species nearby" (quests Phase 3, RFC-quests §3/§4/§13).

Asks iNat which plant species are frequently observed near a GPS point (with an
ADAPTIVE radius), keeps the recognizable ones, applies optional theme safety
(edible → non-toxic only), bridges each to our corpus via `_latin_key`, and
returns the top-N as walk cards. Corpus is NOT required — a species we lack is
still a card (iNat name/photo, plant_id null).
"""
import calendar
import hashlib
import logging
from datetime import date

import httpx
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inaturalist import INAT_BASE, _HEADERS
from app.services.plant_matching import resolve_latin_to_plants, _latin_key
from app.models.place import QuestPlace, QuestPlaceSet, QuestIssuedBadge
from app.models.device import Device

logger = logging.getLogger(__name__)

_MIN_OBS = 50          # density threshold: below this a place gives only walks, no badge
POINTS_PER_BADGE = 10  # v1 leaderboard: fixed points per badge (PLAN-quests-progression)

# Deterministic anonymous nickname from a device_key (silent identity — no PII,
# no prompt). Stable across calls, so it needn't be stored.
_NICK_ADJ = ["Зелёный", "Лесной", "Тихий", "Быстрый", "Мудрый", "Солнечный", "Росистый",
             "Полевой", "Горный", "Речной", "Утренний", "Вечерний", "Смелый", "Лёгкий",
             "Цветущий", "Хвойный", "Луговой", "Степной", "Северный", "Янтарный"]
_NICK_NOUN = ["Бегемот", "Лис", "Барсук", "Филин", "Ёж", "Зубр", "Олень", "Бобр", "Аист",
              "Сокол", "Рысь", "Глухарь", "Хорёк", "Журавль", "Кабан", "Тетерев", "Енот",
              "Выдра", "Дрозд", "Шмель"]


def auto_nick(device_key) -> str:
    h = int(hashlib.md5(str(device_key).encode()).hexdigest(), 16)
    return (f"{_NICK_ADJ[h % len(_NICK_ADJ)]} "
            f"{_NICK_NOUN[(h // len(_NICK_ADJ)) % len(_NICK_NOUN)]} #{h % 10000:04d}")


def window_label(month: int, day: int) -> str:
    """Half-month machine label, e.g. 'first-half-05'."""
    return f"{'first-half' if day <= 15 else 'second-half'}-{month:02d}"


def _window_month(label: str) -> int:
    return int(label.rsplit("-", 1)[1])


def window_dates(label: str, year: int) -> tuple[date, date]:
    m = _window_month(label)
    if label.startswith("first-half"):
        return date(year, m, 1), date(year, m, 15)
    return date(year, m, 16), date(year, m, calendar.monthrange(year, m)[1])

_RADII_KM = [2, 5, 10, 25]      # adaptive: expand until enough candidates (RFC §13.6)
_MIN_CANDIDATES = 15


async def _species_counts(client, lat, lng, radius_km, month=None):
    """iNat species frequency near a point (Plantae, research-grade), count desc."""
    params = {"lat": lat, "lng": lng, "radius": radius_km, "iconic_taxa": "Plantae",
              "quality_grade": "research", "per_page": 50, "locale": "ru"}
    if month:
        params["month"] = month
    try:
        r = await client.get(f"{INAT_BASE}/observations/species_counts", params=params, headers=_HEADERS)
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("iNat species_counts %s,%s r=%s: %s", lat, lng, radius_km, str(e)[:60])
        return []
    out = []
    for row in results:
        t = row.get("taxon") or {}
        out.append({
            "latin": t.get("name"), "name_ru": t.get("preferred_common_name"),
            "rank": t.get("rank"), "count": row.get("count"),
            "photo": (t.get("default_photo") or {}).get("medium_url"),
        })
    return out


def _recognizable(s: dict) -> bool:
    """A telephone-recognizable card: a named species with a photo. (Mosses/grass
    refinement — by family ancestry — is a later improvement; v1 keeps it simple.)"""
    return s.get("rank") in ("species", "subspecies") and bool(s.get("photo")) and bool(s.get("latin"))


async def build_walk(db: AsyncSession, lat: float, lng: float,
                     month: int | None = None, theme: str | None = None, target: int = 5) -> dict:
    used_radius = _RADII_KM[-1]
    recognizable: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for radius in _RADII_KM:
            species = await _species_counts(client, lat, lng, radius, month=month)
            recognizable = [s for s in species if _recognizable(s)]
            if len(recognizable) >= _MIN_CANDIDATES:
                used_radius = radius
                break

    # Bridge to corpus (plant_id|null) + theme safety (edible → non-toxic only).
    latins = [s["latin"] for s in recognizable]
    plant_map = await resolve_latin_to_plants(db, latins)

    items: list[dict] = []
    for s in recognizable:
        p = plant_map.get(s["latin"])
        if theme == "edible" and p is not None and p.is_toxic:
            continue  # safety: never route an edible-theme walk to a toxic species
        items.append({
            "latin_key": _latin_key(s["latin"]),
            "name": s.get("name_ru") or s["latin"],
            "latin": s["latin"],
            "inat_photo": s.get("photo"),
            "plant_id": str(p.id) if p is not None else None,  # null = no monograph, OK
            "count": s.get("count"),
        })
        if len(items) >= target:
            break

    return {"kind": "walk", "near": {"lat": lat, "lng": lng},
            "radius_km": used_radius, "theme": theme, "items": items}


# ----------------------------------------------------------- Phase 4: species-set

async def compute_species_set(db: AsyncSession, place_id: str, label: str) -> dict:
    """Characteristic species-set of place × half-month window (multi-year iNat
    aggregate over the place bbox). Stores the badge TARGET. v1: iNat month filter
    (whole month) + bbox; half-month/polygon precision lives in badge progress."""
    row = (await db.execute(text(
        "SELECT name, ST_YMin(geom), ST_XMin(geom), ST_YMax(geom), ST_XMax(geom) FROM quest_places WHERE id=:p"),
        {"p": place_id})).first()
    if not row:
        return {"error": "place not found"}
    name, swlat, swlng, nelat, nelng = row
    month = _window_month(label)
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.get(f"{INAT_BASE}/observations/species_counts", headers=_HEADERS, params={
                "nelat": nelat, "nelng": nelng, "swlat": swlat, "swlng": swlng, "month": month,
                "iconic_taxa": "Plantae", "quality_grade": "research", "per_page": 100, "locale": "ru"})
            results = r.json().get("results", []) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            results = []
    recog = [x for x in results
             if (x.get("taxon") or {}).get("rank") in ("species", "subspecies")
             and (x.get("taxon") or {}).get("default_photo")]
    obs_total = sum(x.get("count", 0) for x in recog)
    # Build the key list + per-species display meta together (name/photo come free
    # from the same iNat taxon → store them so place/{id}/set needs no live iNat).
    sset: list[str] = []
    meta: list[dict] = []
    for x in recog[:30]:
        t = x.get("taxon") or {}
        k = _latin_key(t.get("name"))
        if not k:
            continue
        sset.append(k)
        meta.append({"key": k, "latin": t.get("name"),
                     "name": t.get("preferred_common_name"),
                     "photo": (t.get("default_photo") or {}).get("medium_url")})
    if obs_total < _MIN_OBS or len(sset) < 5:
        return {"place": name, "window": label, "skipped": "low_density", "obs_total": obs_total, "species": len(sset)}
    target = max(5, min(15, round(0.6 * len(sset))))
    await db.execute(pg_insert(QuestPlaceSet).values(
        place_id=place_id, window_label=label, species_set=sset, species_meta=meta,
        target=target, obs_total=obs_total
    ).on_conflict_do_update(constraint="uq_place_window", set_={
        "species_set": sset, "species_meta": meta, "target": target,
        "obs_total": obs_total, "computed_at": func.now()}))
    await db.commit()
    return {"place": name, "window": label, "set_size": len(sset), "target": target, "obs_total": obs_total}


# ----------------------------------------------------------- Phase 5: badges

async def _set_for(db, place_id, label):
    return (await db.execute(select(QuestPlaceSet).where(
        QuestPlaceSet.place_id == place_id, QuestPlaceSet.window_label == label))).scalar_one_or_none()


async def badge_progress(db: AsyncSession, device_key: str, place_id: str, label: str, year: int) -> dict:
    """Server-verified progress: distinct set-species this device identified INSIDE
    the polygon during this year's half-month window (from History)."""
    ps = await _set_for(db, place_id, label)
    if not ps:
        return {"error": "no species-set for this place/window"}
    f, t = window_dates(label, year)
    rows = (await db.execute(text("""
        SELECT top_latin FROM identifications
        WHERE device_key = CAST(:dk AS uuid) AND top_latin IS NOT NULL
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND captured_at >= :f AND captured_at < (CAST(:t AS date) + 1)
          AND ST_Contains((SELECT geom FROM quest_places WHERE id=:p),
                          ST_SetSRID(ST_MakePoint(lng, lat), 4326))
    """), {"dk": device_key, "f": f, "t": t, "p": place_id})).all()
    sset = set(ps.species_set or [])
    matched = sorted({k for (tl,) in rows if (k := _latin_key(tl)) in sset})
    return {"badge_id": f"{place_id}:{label}:{year}", "matched": len(matched),
            "target": ps.target, "set_size": len(sset), "matched_keys": matched}


async def claim_badge(db: AsyncSession, device_key: str, place_id: str, label: str, year: int) -> dict:
    """Issue the yearly badge if the device met the target AND the window is open.
    Server stamps an ordinal (scarcity). Idempotent per (badge_id, device)."""
    prog = await badge_progress(db, device_key, place_id, label, year)
    if "error" in prog:
        return prog
    badge_id = prog["badge_id"]
    existing = (await db.execute(select(QuestIssuedBadge).where(
        QuestIssuedBadge.badge_id == badge_id,
        QuestIssuedBadge.device_key == device_key))).scalar_one_or_none()
    if existing:
        return {**prog, "issued": True, "ordinal": existing.ordinal, "already": True}
    _, t = window_dates(label, year)
    if prog["matched"] < (prog["target"] or 10**9):
        return {**prog, "issued": False, "reason": "below_target"}
    if date.today() > t:
        return {**prog, "issued": False, "reason": "window_closed"}
    n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
        QuestIssuedBadge.badge_id == badge_id))).scalar() or 0
    db.add(QuestIssuedBadge(badge_id=badge_id, device_key=device_key, ordinal=n + 1,
                            window_closed=False))
    await db.commit()
    return {**prog, "issued": True, "ordinal": n + 1}


async def badge_shelf(db: AsyncSession, device_key: str) -> list[dict]:
    rows = (await db.execute(select(QuestIssuedBadge).where(
        QuestIssuedBadge.device_key == device_key).order_by(QuestIssuedBadge.issued_at.desc()))).scalars().all()
    return [{"badge_id": b.badge_id, "ordinal": b.ordinal, "issued_at": b.issued_at.isoformat()} for b in rows]


# --------------------------------------------------- Phase 6: places (no live iNat)

def _current_window() -> str:
    t = date.today()
    return window_label(t.month, t.day)


async def _badge_issued(db, device_key, place_id, window, year) -> bool:
    if not device_key:
        return False
    n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
        QuestIssuedBadge.badge_id == f"{place_id}:{window}:{year}",
        QuestIssuedBadge.device_key == device_key))).scalar() or 0
    return n > 0


async def places_near(db: AsyncSession, lat: float, lng: float, device_key=None,
                      radius_km: float = 25, limit: int = 20,
                      window: str | None = None, year: int | None = None) -> dict:
    """Places near a point that have a precomputed species-set for the window —
    for the map/list. Distance/centroid via PostGIS; per-device matched +
    badge_issued so the client can show progress. No live iNat."""
    win = window or _current_window()
    yr = year or date.today().year
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.kind,
               ST_Y(ST_Centroid(p.geom)) AS clat, ST_X(ST_Centroid(p.geom)) AS clng,
               ST_Distance(ST_Centroid(p.geom)::geography,
                           ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography)/1000.0 AS dist,
               ps.target, COALESCE(array_length(ps.species_set,1),0) AS set_size
        FROM quest_places p
        JOIN quest_place_sets ps ON ps.place_id = p.id AND ps.window_label = :win
        WHERE p.geom IS NOT NULL
          AND ST_DWithin(ST_Centroid(p.geom)::geography,
                         ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography, :rad)
        ORDER BY dist LIMIT :lim
    """), {"lat": lat, "lng": lng, "win": win, "rad": radius_km * 1000.0, "lim": limit})).all()

    places = []
    for pid, name, kind, clat, clng, dist, target, set_size in rows:
        matched = 0
        if device_key:
            prog = await badge_progress(db, str(device_key), str(pid), win, yr)
            matched = prog.get("matched", 0) if "error" not in prog else 0
        places.append({
            "id": str(pid), "name": name, "kind": kind,
            "lat": clat, "lng": clng, "distance_km": round(dist, 2),
            "window": win, "set_size": set_size, "target": target,
            "matched": matched, "badge_issued": await _badge_issued(db, device_key, pid, win, yr),
        })
    return {"places": places}


async def place_set(db: AsyncSession, place_id: str, window: str | None = None,
                    device_key=None, year: int | None = None) -> dict:
    """«What to look for here» — species cards from the SAVED set (no live iNat).
    Names/photos come from species_meta (saved at compute) with a corpus fallback;
    plant_id via the latin-key bridge; found = this device identified it in the
    polygon×window."""
    win = window or _current_window()
    yr = year or date.today().year
    ps = await _set_for(db, place_id, win)
    if not ps:
        return {"error": "no species-set for this place/window"}
    place = await db.get(QuestPlace, uuid_or(place_id))

    found_keys: set[str] = set()
    matched = 0
    if device_key:
        prog = await badge_progress(db, str(device_key), str(place_id), win, yr)
        if "error" not in prog:
            found_keys = set(prog.get("matched_keys", []))
            matched = prog.get("matched", 0)

    meta = ps.species_meta or [{"key": k} for k in (ps.species_set or [])]
    # Resolve corpus cards for fallback name/photo + the plant_id bridge.
    plant_map = await resolve_latin_to_plants(db, [m["key"] for m in meta])
    items = []
    for m in meta:
        key = m["key"]
        p = plant_map.get(key)
        latin = m.get("latin") or (key[:1].upper() + key[1:])
        items.append({
            "latin_key": key,
            "name": m.get("name") or (p.name if p else None) or latin,
            "latin": latin,
            "inat_photo": m.get("photo") or (p.photo_url if p else None),
            "plant_id": str(p.id) if p else None,
            "found": key in found_keys,
        })
    return {"place": {"id": str(place_id), "name": place.name if place else None,
                      "window": win, "set_size": len(meta), "target": ps.target,
                      "matched": matched, "badge_issued": await _badge_issued(db, device_key, place_id, win, yr)},
            "items": items}


# --------------------------------------------------- Phase 6: leaderboard (v1)

async def leaderboard(db: AsyncSession, device_key=None, limit: int = 20) -> dict:
    """Global all-time leaderboard. score = POINTS_PER_BADGE × issued badges
    (server-counted — client never sends a score). Dense rank by score desc,
    tie-break earliest badge. `me` returned even when outside the top."""
    rows = (await db.execute(text("""
        SELECT device_key, count(*) AS badges, min(issued_at) AS first_at
        FROM quest_issued_badges GROUP BY device_key
    """))).all()
    ranked = sorted(rows, key=lambda r: (-r.badges, r.first_at))

    nicks: dict = {}
    if ranked:
        devs = (await db.execute(select(Device.device_key, Device.nickname).where(
            Device.device_key.in_([r.device_key for r in ranked])))).all()
        nicks = {dk: nk for dk, nk in devs}

    # dense rank
    rank_of: dict = {}
    rank, prev = 0, None
    for r in ranked:
        score = r.badges * POINTS_PER_BADGE
        if score != prev:
            rank += 1
            prev = score
        rank_of[r.device_key] = rank

    def entry(r):
        return {"rank": rank_of[r.device_key],
                "nick": nicks.get(r.device_key) or auto_nick(r.device_key),
                "score": r.badges * POINTS_PER_BADGE, "badges": r.badges}

    top = [entry(r) for r in ranked[:limit]]
    me = None
    if device_key:
        dk = uuid_or(device_key)
        hit = next((r for r in ranked if r.device_key == dk), None)
        me = entry(hit) if hit else {"rank": None, "nick": auto_nick(dk), "score": 0, "badges": 0}
    return {"me": me, "top": top}


def uuid_or(v):
    import uuid as _u
    return v if isinstance(v, _u.UUID) else _u.UUID(str(v))
