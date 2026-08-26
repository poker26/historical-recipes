"""Walk engine — "5 species nearby" (quests Phase 3, RFC-quests §3/§4/§13).

Asks iNat which plant species are frequently observed near a GPS point (with an
ADAPTIVE radius), keeps the recognizable ones, applies optional theme safety
(edible → non-toxic only), bridges each to our corpus via `_latin_key`, and
returns the top-N as walk cards. Corpus is NOT required — a species we lack is
still a card (iNat name/photo, plant_id null).
"""
import asyncio
import calendar
import hashlib
import logging
import math
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inaturalist import INAT_BASE, _HEADERS
from app.services import gbif
from app.services.plant_matching import resolve_latin_to_plants, _latin_key, synonym_map, canonical_key
from app.models.place import QuestPlace, QuestPlaceSet, QuestIssuedBadge
from app.models.device import Device, QuestFollow
from app.models.identification import Identification
from app.models.plant import Plant

logger = logging.getLogger(__name__)

_MIN_OBS = 50          # density threshold: below this a place gives only walks, no badge
POINTS_PER_BADGE = 10  # legacy v1 constant (superseded by tier points below)

# Soft progression: each place×window badge has up to 3 TIERS — a low entry rung so
# a newcomer earns a badge in one short walk, then steps up (HANDOFF-gamification-
# tiers / PLAN-quests-progression). новичок=3 species, любитель≈mid, мастер=target.
_TIER_NAMES = {1: "новичок", 2: "любитель", 3: "мастер"}
_TIER_POINTS = {1: 5, 2: 15, 3: 30}


def tier_thresholds(target, set_size: int) -> list[dict]:
    """The tier ladder for one badge: need = [3, round((3+target)/2), target],
    clamped 1 ≤ t1 < t2 < t3 ≤ set_size. For a tiny set that can't fit 3 strictly
    increasing rungs the ladder COLLAPSES to the lower tiers (drop the rungs that
    won't fit) rather than emitting equal/over-size thresholds — points/names stay
    pinned to the tier number."""
    tgt = target or max(1, set_size)
    raw = [3, round((3 + tgt) / 2), tgt]
    needs: list[int] = []
    for n in raw:
        n = max(1, min(int(n), set_size))                 # into [1, set_size]
        n = max(n, (needs[-1] + 1) if needs else 1)       # strictly increasing
        if n > set_size:                                  # no room left → drop this + higher
            break
        needs.append(n)
    return [{"tier": i + 1, "name": _TIER_NAMES[i + 1], "need": n, "points": _TIER_POINTS[i + 1]}
            for i, n in enumerate(needs)]

# Deterministic anonymous nickname from a device_key (silent identity — no PII,
# no prompt). Stable across calls, so it needn't be stored.
_NICK_ADJ = ["Зелёный", "Лесной", "Тихий", "Быстрый", "Мудрый", "Солнечный", "Росистый",
             "Полевой", "Горный", "Речной", "Утренний", "Вечерний", "Смелый", "Лёгкий",
             "Цветущий", "Хвойный", "Луговой", "Степной", "Северный", "Янтарный"]
# Plant nouns (masculine, to agree with the masculine adjectives) — animals confused
# users («бодрый укроп» feedback 2026-06-21).
_NICK_NOUN = ["Укроп", "Подорожник", "Одуванчик", "Зверобой", "Чабрец", "Тысячелистник",
              "Клевер", "Вереск", "Папоротник", "Можжевельник", "Шиповник", "Боярышник",
              "Василёк", "Лопух", "Чертополох", "Хвощ", "Репейник", "Иван-чай", "Лютик", "Пырей"]


def auto_nick(device_key) -> str:
    h = int(hashlib.md5(str(device_key).encode()).hexdigest(), 16)
    return (f"{_NICK_ADJ[h % len(_NICK_ADJ)]} "
            f"{_NICK_NOUN[(h // len(_NICK_ADJ)) % len(_NICK_NOUN)]} #{h % 10000:04d}")


# Public handle (short slug) — derived deterministically from device_key (one-way),
# so backfill is idempotent and collision-free at our scale. Stored + indexed for
# reverse lookup (handle → device). device_key never appears on public surfaces.
_HANDLE_ABC = "abcdefghijklmnopqrstuvwxyz0123456789"


def auto_handle(device_key) -> str:
    h = int(hashlib.md5(("handle:" + str(device_key)).encode()).hexdigest()[:16], 16)
    out = []
    for _ in range(9):
        out.append(_HANDLE_ABC[h % 36])
        h //= 36
    return "".join(out)


def window_label(month: int, day: int) -> str:
    """Monthly machine label, e.g. 'month-05' (was half-monthly 'first-half-05' — too
    short a cadence; quests/badges are now per-month, named «… — июль»). `day` kept in
    the signature for call-site compatibility but no longer used."""
    return f"month-{month:02d}"


def _window_month(label: str) -> int:
    return int(label.rsplit("-", 1)[1])


def window_dates(label: str, year: int) -> tuple[date, date]:
    m = _window_month(label)
    last = calendar.monthrange(year, m)[1]
    if label.startswith("month-"):          # monthly window = whole month
        return date(year, m, 1), date(year, m, last)
    if label.startswith("first-half"):       # legacy half-month (historical badges)
        return date(year, m, 1), date(year, m, 15)
    return date(year, m, 16), date(year, m, last)

_RADII_KM = [2, 5, 10, 25]      # adaptive: expand until enough candidates (RFC §13.6)
_MIN_CANDIDATES = 15
_NEAR_TIE = 0.85                # badge credit if a set species scores ≥85% of the top
                                # candidate (sibling-species ranking is near-random)


def _credit_keys(cands, top_latin, top_score, target: set, syn: dict | None = None) -> tuple[set, set]:
    """Lenient Q1 match (HANDOFF-identify-improvements) of ONE identification's
    candidate list against a `target` set of latin_keys. Returns (exact, soft) ORIGINAL
    target keys: `exact` = a target species that is the top candidate OR a near-tie
    (score ≥ 85% of top); `soft` = genus-level «almost». Comparison is in CANONICAL
    (accepted-name) space via `syn` (syn_key→accepted_key), so an old synonym in the set
    and the accepted name from the engine (Betonica↔Stachys officinalis) still credit.
    Single source of truth for badge / found-state credit."""
    syn = syn or {}

    def canon(k):
        return syn.get(k, k) if k else k

    # accepted-key → original target key (so we return the ORIGINAL key for counting)
    target_canon: dict[str, str] = {}
    for k in target:
        target_canon.setdefault(canon(k), k)
    target_genera = {ck.split(" ")[0] for ck in target_canon if ck}
    exact, soft = set(), set()
    cands = cands or []
    if not cands:
        ck = canon(_latin_key(top_latin))
        if ck in target_canon:
            exact.add(target_canon[ck])
        return exact, soft
    ts = top_score or max((c.get("score") or 0) for c in cands) or 1.0
    for c in cands:
        ck = canon(_latin_key(c.get("latin")))
        if ck in target_canon and (c.get("score") or 0) >= ts * _NEAR_TIE:
            exact.add(target_canon[ck])
    top_g = (canon(_latin_key((cands[0] or {}).get("latin"))) or " ").split(" ")[0]
    if top_g and top_g in target_genera:
        soft |= {k for k in target if canon(k).split(" ")[0] == top_g}
    return exact, soft


def _confident_latins(cands, top_latin, top_score) -> list[str]:
    """The full latin names this identification confidently asserts — top + near-ties
    (score ≥ 85% of top). Used by biotope-mastery to credit the species the user
    actually found (resolved to a plant), independent of any target set."""
    cands = cands or []
    if not cands:
        return [top_latin] if top_latin else []
    ts = top_score or max((c.get("score") or 0) for c in cands) or 1.0
    return [c.get("latin") for c in cands
            if c.get("latin") and (c.get("score") or 0) >= ts * _NEAR_TIE]


# «Знаток биотопа» tier ladders (RFC-biotope-mastery §B). Thresholds tuned BY BIOTOPE
# RICHNESS — a forest/meadow holds far more characteristic species than a bog or a
# rocky slope, so «мастер» must be heavier where the flora is richer (the scarcity is
# the feature). Keys are the canonical biotopes a live GPS point can resolve to
# (worldcover._CLASS_BIOTOPE). Unknown biotope → _DEFAULT_BIOTOPE_TIERS.
_DEFAULT_BIOTOPE_TIERS = [5, 15, 40]
_BIOTOPE_TIERS: dict[str, list[int]] = {
    "лес": [5, 15, 40],
    "луг": [5, 12, 30],
    "поле/сорное": [5, 12, 30],
    "сады/парки": [5, 12, 30],
    "кустарники/заросли": [4, 10, 24],
    "водное/прибрежное": [3, 8, 18],
    "болото/сырое": [3, 8, 18],
    "каменистые/скалистые склоны": [3, 7, 15],
    "пески/дюны/обнажения": [3, 7, 15],
    "горы/предгорья": [3, 7, 15],
}


def _biotope_tier_ladder(biotope: str) -> list[dict]:
    """The 3-rung Новичок/Любитель/Мастер ladder for a biotope (need-counts by
    richness), shaped like place-badge tiers so the client renders them identically."""
    needs = _BIOTOPE_TIERS.get(biotope, _DEFAULT_BIOTOPE_TIERS)
    return [{"tier": i + 1, "name": _TIER_NAMES[i + 1], "need": n, "points": _TIER_POINTS[i + 1]}
            for i, n in enumerate(needs)]


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


async def nearby(db: AsyncSession, lat: float, lng: float, biotope: str | None = None,
                 month: int | None = None, limit: int = 15,
                 device_key: str | None = None) -> dict:
    """«Растения рядом» — the cold-start base (RFC-cold-start-nearby): live iNat
    frequency near the point, RANKED so habitat-appropriate corpus species lead. The
    point's biotope (OSM landcover at the GPS, or the explicit `biotope`) fixes the
    «I'm in a field but the radius pulled forest species» problem. Works ANYWHERE —
    no precomputed place needed.

    Returns up to `limit` ranked species + `has_more` (HANDOFF-identify-improvements
    Q2: the client shows 5 and pages «другие» locally instead of hitting a 5-item dead
    end). Soft filter: biotope-matching first, then corpus, then the rest; iNat-
    frequency order within each tier."""
    from app.services.worldcover import biotope_at

    # biotope of the point from the local WorldCover raster (no egress, no Overpass
    # rate-limit) — the «field vs forest» primitive. Explicit `biotope` overrides.
    bios = {biotope} if biotope else await asyncio.to_thread(biotope_at, lat, lng)
    used_radius = _RADII_KM[-1]
    recognizable: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for radius in _RADII_KM:
            species = await _species_counts(client, lat, lng, radius, month=month)
            recognizable = [s for s in species if _recognizable(s)]
            if len(recognizable) >= _MIN_CANDIDATES:
                used_radius = radius
                break

    plant_map = await resolve_latin_to_plants(db, [s["latin"] for s in recognizable])
    # corpus plants whose habitat matches the point's biotope
    match_ids: set[str] = set()
    if bios:
        pids = [str(p.id) for p in plant_map.values() if p]
        if pids:
            match_ids = {r[0] for r in (await db.execute(text(
                "SELECT DISTINCT plant_id::text FROM plant_biotopes "
                "WHERE plant_id = ANY(cast(:ids as uuid[])) AND biotope = ANY(:bs)"),
                {"ids": pids, "bs": list(bios)})).all()}

    def _tier(s: dict) -> int:
        p = plant_map.get(s["latin"])
        if p is not None and str(p.id) in match_ids:
            return 0          # corpus + habitat-appropriate → lead
        if p is not None:
            return 1          # in corpus (tap → monograph)
        return 2              # iNat-only, no monograph

    syn = await synonym_map(db)
    items, seen = [], set()
    for s in sorted(recognizable, key=_tier):   # stable → iNat-frequency order kept within tier
        k = _latin_key(s["latin"])
        if k in seen:
            continue
        seen.add(k)
        p = plant_map.get(s["latin"])
        items.append({
            "latin_key": k, "name": s.get("name_ru") or s["latin"], "latin": s["latin"],
            "species_key": canonical_key(k, syn),   # accepted-name key for the verdict
            "inat_photo": s.get("photo"), "plant_id": str(p.id) if p is not None else None,
            "count": s.get("count"),
            "biotope_match": bool(p is not None and str(p.id) in match_ids),
            "found": False,
        })

    pool = len(items)
    shown = items[:limit]
    # found-state + «Знаток биотопа» embed (RFC-biotope-mastery): the card shows «✓
    # Найдено» for species this device already identified, and «Рядом» surfaces the
    # point-biotope mastery bar without a second request. Both are device-scoped.
    found_count = None
    bio_progress = None
    if device_key:
        found = await _device_found_keys(db, device_key, {it["latin_key"] for it in shown})
        for it in shown:
            it["found"] = it["latin_key"] in found
        found_count = sum(1 for it in shown if it["found"])
        primary = sorted(bios)[0] if bios else None
        if primary:
            bio_progress = await biotope_progress(db, device_key, primary)
    return {"kind": "nearby", "near": {"lat": lat, "lng": lng}, "radius_km": used_radius,
            "biotopes": sorted(bios), "items": shown,
            "has_more": pool > limit, "pool_size": pool,
            "found_count": found_count, "biotope_progress": bio_progress}


# ----------------------------------------------------------- Phase 4: species-set

async def compute_species_set(db: AsyncSession, place_id: str, label: str,
                              force: bool = False) -> dict:
    """Characteristic species-set of place × half-month window (multi-year iNat
    aggregate over the place bbox). Stores the badge TARGET. v1: iNat month filter
    (whole month) + bbox; half-month/polygon precision lives in badge progress.

    `force=True` bypasses the density floor (`_MIN_OBS` / 5-species minimum) — for
    TEST places in sparse areas where some observations exist but not 50. The set is
    still corpus-bridged; it just builds from whatever ≥1 corpus species are present."""
    row = (await db.execute(text(
        "SELECT name, ST_YMin(geom), ST_XMin(geom), ST_YMax(geom), ST_XMax(geom) FROM quest_places WHERE id=:p"),
        {"p": place_id})).first()
    if not row:
        return {"error": "place not found"}
    name, swlat, swlng, nelat, nelng = row
    month = _window_month(label)
    # Retry iNat; a FAILED call must NOT be read as obs_total=0 → false low_density
    # (that silently lost rich places like Нескучный сад: 235 obs → 0). results stays
    # None until a 200 lands; if every attempt fails we return a distinct inat_error
    # (transient, retry later), NOT low_density. A 200 with [] IS genuinely empty.
    results = None
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                r = await client.get(f"{INAT_BASE}/observations/species_counts", headers=_HEADERS, params={
                    "nelat": nelat, "nelng": nelng, "swlat": swlat, "swlng": swlng, "month": month,
                    "iconic_taxa": "Plantae", "quality_grade": "research", "per_page": 100, "locale": "ru"})
                if r.status_code == 200:
                    results = r.json().get("results", [])
                    break
                await asyncio.sleep(2 * (attempt + 1))
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(1.5 * (attempt + 1))
    if results is None:
        return {"place": name, "window": label, "skipped": "inat_error"}
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
    # CORPUS-ONLY: keep only species we have a monograph for, so every badge species
    # rewards the finder with info (a species we lack gives no extra knowledge). obs_total
    # (density signal) stays over ALL recognizable species; the badge SET is corpus-bridged.
    plant_map = await resolve_latin_to_plants(db, sset)
    sset = [k for k in sset if plant_map.get(k)]
    meta = [m for m in meta if plant_map.get(m["key"])]
    min_obs = 0 if force else _MIN_OBS
    min_species = 1 if force else 5
    if obs_total < min_obs or len(sset) < min_species:
        return {"place": name, "window": label, "skipped": "low_density", "obs_total": obs_total, "species": len(sset)}
    target = max(1 if force else 5, min(15, round(0.6 * len(sset)) or 1))
    await db.execute(pg_insert(QuestPlaceSet).values(
        place_id=place_id, window_label=label, species_set=sset, species_meta=meta,
        target=target, obs_total=obs_total
    ).on_conflict_do_update(constraint="uq_place_window", set_={
        "species_set": sset, "species_meta": meta, "target": target,
        "obs_total": obs_total, "computed_at": func.now()}))
    await db.commit()
    return {"place": name, "window": label, "set_size": len(sset), "target": target, "obs_total": obs_total}


# ------------------------------------------------- custom quests (RFC-custom-quests)

async def _inat_species_bbox(swlat, swlng, nelat, nelng, month=None) -> dict[str, dict]:
    """{binomial: {count, photo, name}} of recognizable plants (rank species/subsp +
    photo) from iNat species_counts over the bbox. `month=None` → all-season (custom
    quests want the full local flora, not just this half-month). Empty {} on iNat failure
    (a failed call must NOT read as 0 — same lesson as compute_species_set)."""
    results = None
    params = {"nelat": nelat, "nelng": nelng, "swlat": swlat, "swlng": swlng,
              "iconic_taxa": "Plantae", "quality_grade": "research", "per_page": 100, "locale": "ru"}
    if month:
        params["month"] = month
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                r = await client.get(f"{INAT_BASE}/observations/species_counts", headers=_HEADERS, params=params)
                if r.status_code == 200:
                    results = r.json().get("results", []); break
                await asyncio.sleep(2 * (attempt + 1))
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(1.5 * (attempt + 1))
    if results is None:
        return {}
    out: dict[str, dict] = {}
    for x in results:
        t = x.get("taxon") or {}
        if t.get("rank") not in ("species", "subspecies") or not t.get("default_photo"):
            continue
        latin = t.get("name")
        if not latin:
            continue
        out[latin] = {"count": x.get("count", 0),
                      "photo": (t.get("default_photo") or {}).get("medium_url"),
                      "name": t.get("preferred_common_name")}
    return out


def _biotope_quest_name(bios: set) -> str:
    return f"Квест · {sorted(bios)[0]}" if bios else "Мой квест"


# Friendly one-word biotope labels for quest NAMES (the raw keys are slash-compounds like
# «поле/сорное» — «сорное» reads badly). Default: take the part before the slash.
_BIOTOPE_NAME = {
    "лес": "лес", "луг": "луг", "поле/сорное": "поле", "сады/парки": "парк",
    "кустарники/заросли": "кустарники", "водное/прибрежное": "берег",
    "болото/сырое": "болото", "каменистые/скалистые склоны": "скалы",
    "пески/дюны/обнажения": "пески", "горы/предгорья": "горы",
}


def _biotope_label(bio: str) -> str:
    return _BIOTOPE_NAME.get(bio, bio.split("/")[0])


def _custom_quest_name(topo: dict | None, bios: set) -> str:
    """Auto-name a custom quest from the nearest OSM toponym (no user input → no
    moderation): a named park/forest wins outright; otherwise pair the biotope with the
    nearest locality («Лес · Сосновка») so each quest is distinct, not all «лес»."""
    bio = _biotope_label(sorted(bios)[0]) if bios else None
    bio_cap = (bio[:1].upper() + bio[1:]) if bio else None
    green = (topo or {}).get("green")
    loc = (topo or {}).get("locality")
    name = None
    if green:
        name = green
    elif loc and bio_cap:
        name = f"{bio_cap} · {loc}"
    elif loc:
        name = loc
    elif bio:
        name = f"Квест · {bio}"
    else:
        name = "Мой квест"
    return name.strip()[:60]


def _bbox_around(lat: float, lng: float, km: float) -> tuple[float, float, float, float]:
    """(swlat, swlng, nelat, nelng) for a box of half-extent `km` around the point."""
    dlat = km / 111.0
    dlng = km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng


# Диапазоны радиуса конструктора (RFC-v2 §7.3): «вело» в v2.0 = пресет большего
# радиуса, не отдельный роутинг-режим.
_EST_RANGE = {"walk": (0.3, 10.0), "bike": (1.0, 25.0)}
_EST_MIN_SPECIES = 8       # ниже — область «тонкая», предлагаем расширение
_EST_TTL_S = 1800.0
_ESTIMATE_CACHE: dict[tuple, tuple[float, dict]] = {}


async def estimate_custom_area(db: AsyncSession, lat: float, lng: float,
                               radius_km: float, movement_mode: str = "walk",
                               month: int | None = None) -> dict:
    """Дешёвая оценка области ДО генерации кастомной прогулки (RFC-v2 §7.3,
    драфт §12.4): один iNat species_counts по bbox + биотоп точки. «Никаких
    тупиков»: тонкая область получает min_viable/suggested радиус, а не ошибку.
    Кэш в памяти процесса (клиент дополнительно дебаунсит drag)."""
    import time as _time
    lo, hi = _EST_RANGE.get(movement_mode, _EST_RANGE["walk"])
    radius_km = max(lo, min(hi, float(radius_km)))
    key = (round(lat, 3), round(lng, 3), round(radius_km, 1), movement_mode, month or 0)
    now = _time.time()
    hit = _ESTIMATE_CACHE.get(key)
    if hit and now - hit[0] < _EST_TTL_S:
        return hit[1]
    swlat, swlng, nelat, nelng = _bbox_around(lat, lng, radius_km)
    species = await _inat_species_bbox(swlat, swlng, nelat, nelng, month=month)
    from app.services.worldcover import biotope_at
    bios = await asyncio.to_thread(biotope_at, lat, lng)
    n = len(species)
    confirmed = 0
    if species:
        plant_map = await resolve_latin_to_plants(db, list(species.keys()))
        confirmed = sum(1 for p in plant_map.values() if p)
    if n >= _EST_MIN_SPECIES:
        viability, reason, min_viable, suggested = "ready", None, None, None
    else:
        # видовое богатство растёт ~ с площадью (радиус²) → корневая экстраполяция
        factor = math.sqrt(_EST_MIN_SPECIES / max(n, 1))
        min_viable = round(min(hi, radius_km * factor), 1)
        suggested = round(min(hi, min_viable * 1.15), 1)
        if n == 0:
            viability, reason = "unavailable", "no_observations_or_inat_error"
        elif min_viable > radius_km:
            viability, reason = "sparse", "not_enough_species"
        else:
            viability, reason = "ready", None
            min_viable = suggested = None
    result = {
        "viability": viability, "reason": reason,
        "radius_km": radius_km, "movement_mode": movement_mode,
        "radius_range_km": [lo, hi],
        "candidate_count": n, "confirmed_count": confirmed,
        "expected_count": max(0, n - confirmed),
        "min_viable_radius_km": min_viable, "suggested_radius_km": suggested,
        "biotopes": sorted(bios) if bios else [],
    }
    _ESTIMATE_CACHE[key] = (now, result)
    if len(_ESTIMATE_CACHE) > 500:      # не даём кэшу расти бесконечно
        for k in list(_ESTIMATE_CACHE)[:100]:
            _ESTIMATE_CACHE.pop(k, None)
    return result


async def compute_custom_set(db: AsyncSession, place_id: str, label: str,
                             point_lat: float, point_lng: float) -> dict:
    """Species-set for a CUSTOM (user-ordered) quest at a point. Layer-1 «точно растёт»
    = iNat ∪ GBIF inside the circle (corpus-bridged). Layer-2 «ожидается» (only when
    Layer-1 is thin) = species characteristic of the point's biotope (plant_biotopes)
    that are also recorded across the REGION (GBIF). Each species carries a `confidence`
    flag (confirmed/expected) in species_meta; both count toward the badge (user's call)."""
    row = (await db.execute(text(
        "SELECT name, ST_YMin(geom), ST_XMin(geom), ST_YMax(geom), ST_XMax(geom) FROM quest_places WHERE id=:p"),
        {"p": place_id})).first()
    if not row:
        return {"error": "place not found"}
    name = row[0]

    # --- Layer 1: подтверждённые виды ОКРЕСТНОСТИ (~5 км), весь сезон. Радиус ПОИСКА
    # шире walkable-круга: в точном 1 км у дачи может быть ~0 находок, а окрестный лес/луг
    # хорошо отснят — и эти виды почти наверняка растут и в круге (iNat ∪ GBIF). ---
    s1lat, s1lng, n1lat, n1lng = _bbox_around(point_lat, point_lng, 5.0)
    inat = await _inat_species_bbox(s1lat, s1lng, n1lat, n1lng, month=None)
    gbif_local = await gbif.species_in_bbox(s1lat, s1lng, n1lat, n1lng, max_records=1500)
    confirmed: dict[str, dict] = {}   # latin_key -> {latin, count, photo, name}
    for latin, info in inat.items():
        k = _latin_key(latin)
        if not k:
            continue
        e = confirmed.setdefault(k, {"latin": latin, "count": 0, "photo": None, "name": None})
        e["count"] += info["count"]; e["photo"] = info["photo"]; e["name"] = info["name"]
    for latin, c in gbif_local.items():
        k = _latin_key(latin)
        if not k:
            continue
        e = confirmed.setdefault(k, {"latin": latin, "count": 0, "photo": None, "name": None})
        e["count"] += c
    obs_total = sum(e["count"] for e in confirmed.values())
    # Corpus-bridge so every badge species rewards the finder with a monograph.
    plant_map = await resolve_latin_to_plants(db, list(confirmed.keys()))
    confirmed = {k: v for k, v in confirmed.items() if plant_map.get(k)}

    # --- Layer 2: «ожидается» (биотоп точки × известные в регионе), если Layer-1 жидкий ---
    expected: dict[str, str] = {}     # latin_key -> latin
    pl_src: str | None = None         # plantarium attribution URL when it contributed
    if len(confirmed) < 8:
        from app.services.worldcover import biotope_at
        from app.services import plantarium
        bios = await asyncio.to_thread(biotope_at, point_lat, point_lng)
        if bios:
            brows = (await db.execute(text(
                "SELECT DISTINCT p.name_latin FROM plant_biotopes pb JOIN plants p ON p.id = pb.plant_id "
                "WHERE pb.biotope = ANY(cast(:bios as text[])) AND p.name_latin IS NOT NULL"),
                {"bios": list(bios)})).all()
            biotope_keys = {}
            for (lat_name,) in brows:
                k = _latin_key(lat_name)
                if k:
                    biotope_keys[k] = lat_name
            # «известные в регионе» — GBIF по широкому боксу (~±0.6° lat / ±0.9° lng) +
            # plantarium регион-чек-лист (РФ-усиление, on-demand по одному, с атрибуцией).
            reg = await gbif.species_in_bbox(point_lat - 0.6, point_lng - 0.9,
                                             point_lat + 0.6, point_lng + 0.9, max_records=2000)
            region_keys = {kk for kk in (_latin_key(l) for l in reg) if kk}
            region_name = await plantarium.region_at(point_lat, point_lng)
            pl_latins, pl_url = await plantarium.region_species(region_name)
            if pl_latins:
                region_keys |= {kk for kk in (_latin_key(l) for l in pl_latins) if kk}
            for k, lat_name in biotope_keys.items():
                if k in region_keys and k not in confirmed:
                    expected[k] = lat_name
            if expected and pl_url:
                pl_src = pl_url

    # --- build set + meta (confirmed first, then expected) ---
    items: list[dict] = []
    for k, e in confirmed.items():
        p = plant_map.get(k)
        items.append({"key": k, "latin": e["latin"],
                      "name": e.get("name") or (p.name if p else None),
                      "photo": e.get("photo"), "confidence": "confirmed"})
    if expected:
        exp_plants = await resolve_latin_to_plants(db, list(expected.keys()))
        for k, lat_name in expected.items():
            p = exp_plants.get(k)
            if not p:
                continue
            items.append({"key": k, "latin": lat_name, "name": p.name,
                          "photo": None, "confidence": "expected"})
    items = items[:30]
    sset = [it["key"] for it in items]
    if not sset:
        return {"place": name, "skipped": "empty", "confirmed": 0, "expected": 0}
    target = max(5, min(15, round(0.6 * len(sset)) or 5))
    await db.execute(pg_insert(QuestPlaceSet).values(
        place_id=place_id, window_label=label, species_set=sset, species_meta=items,
        target=target, obs_total=obs_total
    ).on_conflict_do_update(constraint="uq_place_window", set_={
        "species_set": sset, "species_meta": items, "target": target,
        "obs_total": obs_total, "computed_at": func.now()}))
    await db.commit()
    n_conf = sum(1 for it in items if it["confidence"] == "confirmed")
    return {"place": name, "window": label, "set_size": len(sset), "target": target,
            "confirmed": n_conf, "expected": len(sset) - n_conf, "obs_total": obs_total,
            "plantarium_source": pl_src}


async def create_custom_quest(db: AsyncSession, lat: float, lng: float,
                              radius_km: float = 1.0, window: str | None = None) -> dict:
    """Build a circle quest-place around the point (biotope-themed name), persist it
    (kind='custom', osm_id NULL), and compute its custom species-set. Returns the place
    so the client opens it like any other PlaceQuestScreen."""
    win = window or _current_window()
    rad_m = max(300.0, min(2500.0, radius_km * 1000.0))
    # Dedup: if a custom quest with a set for this window already exists within ~250 m,
    # reuse it instead of piling up duplicates when «Заказать» is tapped again.
    dup = (await db.execute(text("""
        SELECT p.id::text, p.name, ST_Y(ST_Centroid(p.geom)), ST_X(ST_Centroid(p.geom)),
               ps.target, COALESCE(array_length(ps.species_set,1),0)
        FROM quest_places p JOIN quest_place_sets ps ON ps.place_id=p.id AND ps.window_label=:win
        WHERE p.kind='custom'
          AND ST_DWithin(ST_Centroid(p.geom)::geography,
                         ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography, 250)
        ORDER BY ST_Distance(ST_Centroid(p.geom)::geography,
                             ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography)
        LIMIT 1
    """), {"win": win, "lng": lng, "lat": lat})).first()
    if dup:
        pid, nm, clat, clng, tgt, ss = dup
        return {"place_id": pid, "name": nm, "lat": clat, "lng": clng, "kind": "custom",
                "window": win, "radius_km": radius_km, "set_size": ss, "target": tgt,
                "status": "ok", "reused": True}
    from app.services.worldcover import biotope_at
    from app.services import plantarium
    bios = await asyncio.to_thread(biotope_at, lat, lng)
    # Auto-name from the nearest OSM toponym (no user free-text → no profanity moderation);
    # fall back to the biotope name if the reverse-geocode fails.
    topo = await plantarium.toponym_at(lat, lng)
    qname = _custom_quest_name(topo, bios)
    row = (await db.execute(text("""
        INSERT INTO quest_places (id, osm_id, name, kind, geom, area)
        VALUES (gen_random_uuid(), NULL, :name, 'custom',
            ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography, :rad)::geometry),
            pi() * :rad * :rad)
        RETURNING id::text, ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom))
    """), {"name": qname, "lng": lng, "lat": lat, "rad": rad_m})).first()
    await db.commit()
    pid, clat, clng = row
    res = await compute_custom_set(db, pid, win, lat, lng)
    out = {"place_id": pid, "name": qname, "lat": clat, "lng": clng, "kind": "custom",
           "window": win, "radius_km": rad_m / 1000.0,
           "biotope": (sorted(bios)[0] if bios else None)}
    for kk in ("set_size", "target", "confirmed", "expected", "plantarium_source"):
        if kk in res:
            out[kk] = res[kk]
    out["status"] = "ok" if "set_size" in res else res.get("skipped", "error")
    return out


# --------------------------------------------------- личные места (замер 2026-08-26)
# Снимок попадал внутрь квест-места лишь у 8% устройств с геолокацией: игра ждала,
# что человек придёт в размеченный парк, а он снимает во дворе и на даче. Поэтому
# место заводится САМО вокруг точки, куда человек возвращается. Не вокруг любого
# снимка: нужен признак «я тут бываю» — иначе один кадр из окна поезда плодит мусор.
_PERSONAL_RADIUS_M = 700.0     # круг места
_PERSONAL_NEAR_M = 600.0       # в этом радиусе считаем снимки «тем же местом»
_PERSONAL_MIN_SHOTS = 3        # столько снимков рядом = сюда человек возвращается
_PERSONAL_MAX = 8              # больше личных мест на устройство не заводим


async def ensure_personal_place(db: AsyncSession, device_key: str,
                                lat: float, lng: float) -> dict | None:
    """Завести личное место вокруг точки, если человек тут снимает регулярно.

    Вызывается в фоне после архивации снимка, который не попал ни в одно место.
    Прогресс значка считается из СОХРАНЁННЫХ снимков внутри полигона, поэтому все
    прежние кадры в этой точке засчитываются задним числом — место появляется уже
    с накопленным прогрессом. Возвращает None, когда заводить нечего (мало снимков,
    точка уже внутри места, лимит исчерпан)."""
    dk = _try_uuid(device_key) if device_key else None
    if dk is None or lat is None or lng is None:
        return None
    inside = (await db.execute(text("""
        SELECT 1 FROM quest_places
        WHERE geom IS NOT NULL
          AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)) LIMIT 1"""),
        {"lat": lat, "lng": lng})).first()
    if inside:
        return None                                   # уже есть чей-то квест — не дублируем
    mine = (await db.execute(text(
        "SELECT count(*) FROM quest_places WHERE owner_key = CAST(:dk AS uuid)"),
        {"dk": str(dk)})).scalar() or 0
    if mine >= _PERSONAL_MAX:
        return None
    shots = (await db.execute(text("""
        SELECT count(*) FROM identifications
        WHERE device_key = CAST(:dk AS uuid) AND lat IS NOT NULL
          AND ST_DWithin(ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                         ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :near)"""),
        {"dk": str(dk), "lat": lat, "lng": lng, "near": _PERSONAL_NEAR_M})).scalar() or 0
    if shots < _PERSONAL_MIN_SHOTS:
        return None
    from app.services.worldcover import biotope_at
    from app.services import plantarium
    bios = await asyncio.to_thread(biotope_at, lat, lng)
    topo = await plantarium.toponym_at(lat, lng)
    # Личное место подписываем как личное: голый топоним («Кролики») читается как
    # чужой квест, а «Твоё место · Кролики» сразу объясняет, откуда оно взялось.
    base = _custom_quest_name(topo, bios)
    qname = base if base.startswith("Твоё место") else f"Твоё место · {base}"
    qname = qname[:60]
    row = (await db.execute(text("""
        INSERT INTO quest_places (id, osm_id, name, kind, owner_key, geom, area)
        VALUES (gen_random_uuid(), NULL, :name, 'personal', CAST(:dk AS uuid),
            ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography, :rad)::geometry),
            pi() * :rad * :rad)
        RETURNING id::text"""),
        {"name": qname, "dk": str(dk), "lng": lng, "lat": lat, "rad": _PERSONAL_RADIUS_M})).first()
    await db.commit()
    pid = row[0]
    res = await compute_custom_set(db, pid, _current_window(), lat, lng)
    if "set_size" not in res:
        # Пусто у iNat в этой точке — место без набора бесполезно, убираем за собой.
        await db.execute(text("DELETE FROM quest_places WHERE id = :p"), {"p": pid})
        await db.commit()
        return None
    logger.info("personal place %s created for %s (%s, %d видов)", pid, dk, qname, res["set_size"])
    return {"place_id": pid, "name": qname, "kind": "personal",
            "set_size": res.get("set_size"), "target": res.get("target")}


async def place_participants(db: AsyncSession, place_id: str, window: str | None = None,
                             year: int | None = None, limit: int = 50) -> dict:
    """«Кто проходил этот квест» (Oleg 2026-06-21) — devices that EARNED a badge for this
    place×window×year, to befriend people who did the SAME quest (distinct from the global
    leaderboard). Public + not-blocked only; highest tier per device. («Проходит сейчас» —
    in-progress, not yet a badge — is a later, heavier addition.)"""
    win = window or _current_window()
    yr = year or date.today().year
    badge_id = f"{place_id}:all"   # cumulative badge — one per place
    rows = (await db.execute(text("""
        SELECT d.handle, d.nickname, d.avatar, b.device_key, MAX(b.tier) AS tier
        FROM quest_issued_badges b
        JOIN quest_devices d ON d.device_key = b.device_key
        WHERE b.badge_id = :bid
          AND COALESCE(d.blocked, false) = false
          AND COALESCE(d.activity_public, true) = true
        GROUP BY d.handle, d.nickname, d.avatar, b.device_key
        ORDER BY tier DESC
        LIMIT :lim
    """), {"bid": badge_id, "lim": limit})).all()
    parts = [{
        "handle": handle or auto_handle(dk),
        "nick": nick or auto_nick(dk),
        "avatar": avatar, "tier": tier,
    } for handle, nick, avatar, dk, tier in rows]
    return {"place_id": str(place_id), "window": win, "year": yr,
            "count": len(parts), "participants": parts}


# ----------------------------------------------------------- Phase 5: badges

async def _pool_for(db, place_id) -> tuple[set, int]:
    """КУМУЛЯТИВНАЯ модель «Знаток места» (решение Олега 2026-08-24): пул места =
    объединение ВСЕХ его сезонных наборов, прогресс не сгорает на смене окна.
    Окно больше не дедлайн, а подсказка «что искать сейчас» (place_set). Возвращает
    (pool, target); target по той же формуле от размера пула."""
    rows = (await db.execute(text(
        "SELECT species_set FROM quest_place_sets WHERE place_id = :p"),
        {"p": place_id})).all()
    pool: set[str] = set()
    for (sset,) in rows:
        pool |= set(sset or [])
    target = max(5, min(15, round(0.6 * len(pool)))) if pool else 0
    return pool, target


async def _set_for(db, place_id, label):
    """The set for this window, else — gap-proof fallback — the most recently computed set
    for the place (so a place never «disappears» at a window rollover before the new
    month's set is built, and an old custom quest keeps working)."""
    hit = (await db.execute(select(QuestPlaceSet).where(
        QuestPlaceSet.place_id == place_id, QuestPlaceSet.window_label == label))).scalar_one_or_none()
    if hit:
        return hit
    return (await db.execute(select(QuestPlaceSet).where(
        QuestPlaceSet.place_id == place_id).order_by(
            QuestPlaceSet.computed_at.desc()).limit(1))).scalar_one_or_none()


async def _issued_tiers(db, badge_id: str, device_key) -> dict:
    """{tier: ordinal} already issued to this device for this badge."""
    rows = (await db.execute(select(QuestIssuedBadge.tier, QuestIssuedBadge.ordinal).where(
        QuestIssuedBadge.badge_id == badge_id,
        QuestIssuedBadge.device_key == uuid_or(device_key)))).all()
    return {tr: od for tr, od in rows}


async def badge_progress(db: AsyncSession, device_key: str, place_id: str, label: str, year: int) -> dict:
    """Server-verified progress: distinct set-species this device identified INSIDE
    the polygon during this year's half-month window (from History), mapped onto the
    tier ladder. `current_tier` = highest tier EARNED (need ≤ matched, regardless of
    issuance, 0 = none); `claimable_tier` = highest earned tier NOT yet issued (null
    = nothing to claim); `next_need` = species needed-count of the next not-yet-
    earned rung (null at the top)."""
    sset, pool_target = await _pool_for(db, place_id)
    if not sset:
        return {"error": "no species-set for this place/window"}
    # CUSTOM quests = a fresh personal hunt → count only finds made AFTER the quest was
    # created (Oleg 2026-06-21: «новый квест — новый поиск»; don't auto-credit species
    # already found earlier in an overlapping area). Place quests: CUMULATIVE, all-time
    # (Oleg 2026-08-24) — the window guillotine silently reset progress at month
    # rollover (the Kurils case) and nobody understood why; season is now a HINT
    # (place_set), not a deadline.
    crow = (await db.execute(text(
        "SELECT kind, created_at FROM quest_places WHERE id=:p"), {"p": place_id})).first()
    since = crow[1] if (crow and crow[0] == "custom") else None
    # Probability match (HANDOFF-identify-improvements Q1): the engine ranks sibling
    # species almost at random (Achillea millefolium 45% vs nobilis 44% = noise), so
    # strict top-1 robs a correct find of its badge. Read the FULL candidate list and
    # credit a set species when it's the top OR a near-tie (score ≥ 85% of top), plus
    # a generous GENUS match (top candidate's genus = a set member's genus → «you met
    # a тысячелистник here», the game's goal — not a species exam). User-approved.
    rows = (await db.execute(text("""
        SELECT candidates, top_latin, top_score FROM identifications
        WHERE device_key = CAST(:dk AS uuid)
          AND lat IS NOT NULL AND lng IS NOT NULL
          AND (CAST(:since AS timestamptz) IS NULL OR captured_at >= CAST(:since AS timestamptz))
          AND ST_Contains((SELECT geom FROM quest_places WHERE id=:p),
                          ST_SetSRID(ST_MakePoint(lng, lat), 4326))
    """), {"dk": device_key, "p": place_id, "since": since})).all()
    syn = await synonym_map(db)   # synonym-aware credit (Betonica↔Stachys officinalis)
    exact: set[str] = set()
    soft: set[str] = set()      # genus-level «almost» (shown as «похоже» on the client)
    for cands, top_latin, top_score in rows:
        ex, sf = _credit_keys(cands, top_latin, top_score, sset, syn)
        exact |= ex
        soft |= sf
    soft -= exact
    matched = sorted(exact | soft)
    m = len(matched)
    # Cumulative badge — ONE per place, no window/year in the id. Old monthly ids
    # ({place}:{window}:{year}) are migrated to :all (scripts/migrate_badges_all.py).
    badge_id = f"{place_id}:all"
    tiers = tier_thresholds(pool_target, len(sset))
    issued = await _issued_tiers(db, badge_id, device_key)
    earned = [tr for tr in tiers if tr["need"] <= m]
    current_tier = max((tr["tier"] for tr in earned), default=0)
    claimable_tier = max((tr["tier"] for tr in earned if tr["tier"] not in issued), default=None)
    not_earned = [tr for tr in tiers if tr["need"] > m]
    next_need = not_earned[0]["need"] if not_earned else None
    return {"badge_id": badge_id, "set_size": len(sset), "matched": m,
            "target": pool_target, "tiers": tiers, "current_tier": current_tier,
            "claimable_tier": claimable_tier, "next_need": next_need,
            "matched_keys": matched, "soft_keys": sorted(soft)}


async def claim_badge(db: AsyncSession, device_key: str, place_id: str, label: str, year: int) -> dict:
    """Issue every tier the device has EARNED but not yet claimed, up to the highest.
    Cumulative model: no window_closed — mastery of a place never expires. Each tier
    keeps its own per-tier ordinal (scarcity). Idempotent per (badge_id, device, tier).
    The response headlines the HIGHEST tier granted; `granted` lists all rungs issued
    this call. `label`/`year` stay in the signature for old-client compatibility."""
    prog = await badge_progress(db, device_key, place_id, label, year)
    if "error" in prog:
        return prog
    badge_id = prog["badge_id"]
    issued = await _issued_tiers(db, badge_id, device_key)
    earned_unissued = [tr for tr in prog["tiers"]
                       if tr["need"] <= prog["matched"] and tr["tier"] not in issued]
    if not earned_unissued:
        return {**prog, "issued": False,
                "reason": "already" if issued else "below_first_tier"}
    granted = []
    for tr in earned_unissued:   # ascending tier order
        n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
            QuestIssuedBadge.badge_id == badge_id, QuestIssuedBadge.tier == tr["tier"]))).scalar() or 0
        db.add(QuestIssuedBadge(badge_id=badge_id, device_key=uuid_or(device_key),
                                tier=tr["tier"], points=tr["points"], ordinal=n + 1,
                                window_closed=False))
        granted.append({**tr, "ordinal": n + 1})
    await db.commit()
    top = granted[-1]
    # Re-read so current_tier/claimable_tier reflect the just-issued rungs.
    prog = await badge_progress(db, device_key, place_id, label, year)
    return {**prog, "issued": True, "tier": top["tier"], "name": top["name"],
            "ordinal": top["ordinal"], "points": top["points"], "granted": granted}


async def badge_shelf(db: AsyncSession, device_key: str) -> list[dict]:
    rows = (await db.execute(select(QuestIssuedBadge).where(
        QuestIssuedBadge.device_key == uuid_or(device_key)).order_by(
            QuestIssuedBadge.issued_at.desc()))).scalars().all()
    out = []
    for b in rows:
        parts = b.badge_id.split(":")
        if parts and parts[0] == "meta":         # «Собиратель» / «Постоянство» — без географии
            mk = ":".join(parts[1:])
            out.append({
                "badge_id": b.badge_id, "kind": mk,
                "title": (_META_LADDERS.get(mk) or {}).get("title"),
                "place_id": None, "window": None, "year": None,
                "tier": b.tier, "name": _TIER_NAMES.get(b.tier, ""),
                "points": b.points, "ordinal": b.ordinal,
                "issued_at": b.issued_at.isoformat(),
            })
            continue
        if parts and parts[0] == "biotope":      # «Знаток <биотопа>» — cumulative, no place/window
            out.append({
                "badge_id": b.badge_id, "kind": "biotope",
                "biotope": ":".join(parts[1:]) or None,
                "place_id": None, "window": None, "year": None,
                "tier": b.tier, "name": _TIER_NAMES.get(b.tier, ""),
                "points": b.points, "ordinal": b.ordinal,
                "issued_at": b.issued_at.isoformat(),
            })
            continue
        out.append({
            "badge_id": b.badge_id, "kind": "place",
            "place_id": parts[0] if parts else None,
            "window": parts[1] if len(parts) > 1 else None,
            "year": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
            "tier": b.tier, "name": _TIER_NAMES.get(b.tier, ""),
            "points": b.points, "ordinal": b.ordinal,
            "issued_at": b.issued_at.isoformat(),
        })
    # Resolve place display names here so EVERY caller (own /quests/badges AND the
    # public profile) gets «Мастер · Нескучный сад» — not just the bare tier name.
    names = await _place_names(db, [b.get("place_id") for b in out])
    for b in out:
        if b.get("kind") == "place":
            b["place"] = names.get(b.get("place_id"))
    return out


# ------------------------------------- found-state + «Знаток биотопа» (RFC-biotope-mastery)

async def _device_found_keys(db, device_key, target_keys: set) -> set:
    """Of `target_keys` (latin_keys), the subset this device already correctly
    identified — lenient Q1 credit (near-tie ∪ genus), over ALL its identifications.
    Powers the «✓ Найдено» state in «Рядом» (RFC-biotope-mastery §A)."""
    if not target_keys or not device_key:
        return set()
    dk = _try_uuid(device_key)
    if dk is None:
        return set()
    rows = (await db.execute(text(
        "SELECT candidates, top_latin, top_score FROM identifications "
        "WHERE device_key = CAST(:dk AS uuid)"), {"dk": str(dk)})).all()
    syn = await synonym_map(db)   # synonym-aware found-state
    found: set = set()
    for cands, tl, ts in rows:
        ex, sf = _credit_keys(cands, tl, ts, target_keys, syn)
        found |= ex | sf
    return found


async def _device_biotope_matches(db, device_key) -> dict[str, set]:
    """{biotope: set(latin_key)} — distinct species this device confidently identified
    WHILE STANDING in that biotope (geo-gated) AND characteristic of it (plant_biotopes).
    The geo-attribution IS the reward (RFC-biotope-mastery §B): credit only effortful,
    on-location finds; an un-geotagged shot moves the naturalist level, not mastery.
    One WorldCover raster read per geotagged identification (cheap, local)."""
    from app.services.worldcover import biotope_at
    dk = _try_uuid(device_key)
    if dk is None:
        return {}
    rows = (await db.execute(text(
        "SELECT candidates, top_latin, top_score, lat, lng FROM identifications "
        "WHERE device_key = CAST(:dk AS uuid) AND lat IS NOT NULL AND lng IS NOT NULL"),
        {"dk": str(dk)})).all()
    per_row, all_latins = [], set()
    for cands, tl, ts, lat, lng in rows:
        lats = _confident_latins(cands, tl, ts)
        if lats:
            per_row.append((lats, lat, lng))
            all_latins.update(lats)
    if not all_latins:
        return {}
    plant_map = await resolve_latin_to_plants(db, list(all_latins))   # {latin: Plant|None}
    pids = [str(p.id) for p in plant_map.values() if p]
    pbio: dict[str, set] = {}
    if pids:
        for pid, bio in (await db.execute(text(
            "SELECT plant_id::text, biotope FROM plant_biotopes "
            "WHERE plant_id = ANY(cast(:ids as uuid[]))"), {"ids": pids})).all():
            pbio.setdefault(pid, set()).add(bio)
    out: dict[str, set] = {}
    for lats, lat, lng in per_row:
        point_bios = await asyncio.to_thread(biotope_at, lat, lng)
        if not point_bios:
            continue
        for latin in lats:
            p = plant_map.get(latin)
            if p is None:
                continue
            for bio in point_bios & pbio.get(str(p.id), set()):
                out.setdefault(bio, set()).add(_latin_key(latin) or latin)
    return out


async def biotope_progress(db: AsyncSession, device_key: str, biotope: str,
                           _matches: dict | None = None) -> dict:
    """«Знаток <биотопа>» progress: distinct characteristic species this device found
    ON LOCATION in `biotope`, on the richness-tuned tier ladder. Same shape as the
    place badge_progress so the client renders it identically. `_matches` lets a caller
    (e.g. nearby) reuse a single device-wide pass instead of recomputing."""
    matches = _matches if _matches is not None else await _device_biotope_matches(db, device_key)
    keys = matches.get(biotope, set())
    m = len(keys)
    badge_id = f"biotope:{biotope}"
    tiers = _biotope_tier_ladder(biotope)
    issued = await _issued_tiers(db, badge_id, device_key)
    earned = [tr for tr in tiers if tr["need"] <= m]
    current_tier = max((tr["tier"] for tr in earned), default=0)
    claimable_tier = max((tr["tier"] for tr in earned if tr["tier"] not in issued), default=None)
    not_earned = [tr for tr in tiers if tr["need"] > m]
    next_need = not_earned[0]["need"] if not_earned else None
    return {"badge_id": badge_id, "biotope": biotope, "matched": m, "tiers": tiers,
            "current_tier": current_tier, "claimable_tier": claimable_tier,
            "next_need": next_need, "matched_keys": sorted(keys), "examples": sorted(keys)[:8]}


async def claim_biotope_badge(db: AsyncSession, device_key: str, biotope: str) -> dict:
    """Issue every biotope-mastery tier earned-but-unclaimed (silent UUID award, like
    place badges). No window — mastery is CUMULATIVE, so there is no «window_closed»."""
    prog = await biotope_progress(db, device_key, biotope)
    badge_id = prog["badge_id"]
    issued = await _issued_tiers(db, badge_id, device_key)
    earned_unissued = [tr for tr in prog["tiers"]
                       if tr["need"] <= prog["matched"] and tr["tier"] not in issued]
    if not earned_unissued:
        return {**prog, "issued": False,
                "reason": "already" if issued else "below_first_tier"}
    granted = []
    for tr in earned_unissued:
        n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
            QuestIssuedBadge.badge_id == badge_id, QuestIssuedBadge.tier == tr["tier"]))).scalar() or 0
        db.add(QuestIssuedBadge(badge_id=badge_id, device_key=uuid_or(device_key),
                                tier=tr["tier"], points=tr["points"], ordinal=n + 1,
                                window_closed=False))
        granted.append({**tr, "ordinal": n + 1})
    await db.commit()
    top = granted[-1]
    prog = await biotope_progress(db, device_key, biotope)
    return {**prog, "issued": True, "tier": top["tier"], "name": top["name"],
            "ordinal": top["ordinal"], "points": top["points"], "granted": granted}


# --------------------------------------------------- Phase 6: places (no live iNat)

def _current_window() -> str:
    t = date.today()
    return window_label(t.month, t.day)


# ------------------------------------------- награды без географии (замер 2026-08-26)
# 92% людей вообще не попадают в квест-места, а снимают. Значит награда должна быть
# и за сам факт наблюдения: сколько РАЗНЫХ видов собрано и сколько дней подряд
# человек выходит с камерой. Гео тут не нужно — работает у всех и с первого дня.
_META_LADDERS = {
    "collection": {
        "title": "Собиратель",
        "unit": "видов",
        "rungs": [10, 25, 50],
    },
    "streak": {
        "title": "Постоянство",
        "unit": "дней подряд",
        "rungs": [3, 7, 14],
    },
    # Социальная линейка (идея Олега 2026-08-26). Считаем не «сколько раз кликнули
    # по ссылке», а сколько приглашённых РЕАЛЬНО пользуются приложением — иначе
    # значок фармится переустановками.
    "invites": {
        "title": "Проводник",
        "unit": "приглашённых",
        "rungs": [1, 3, 10],
    },
    "friends": {
        "title": "Компанейский",
        "unit": "взаимных друзей",
        "rungs": [1, 3, 10],
    },
}

# Приглашённый засчитывается, только если он пользуется приложением сам.
_INVITE_MIN_SHOTS = 3
_INVITE_MIN_DAYS = 2
# Позвать «задним числом» можно лишь в первые дни жизни устройства — иначе код
# вводили бы через полгода ради значка знакомому.
_INVITE_WINDOW_DAYS = 21


def _meta_tiers(kind: str) -> list[dict]:
    lad = _META_LADDERS[kind]
    return [{"tier": i + 1, "need": need, "name": _TIER_NAMES[i + 1],
             "points": _TIER_POINTS[i + 1]}
            for i, need in enumerate(lad["rungs"])]


async def _collection_size(db, device_key) -> int:
    """Сколько РАЗНЫХ видов девайс определил за всё время (синонимы схлопнуты).
    Считаем по архиву — значит проверяемо сервером, а не по клиентской истории."""
    rows = (await db.execute(text(
        "SELECT DISTINCT top_latin FROM identifications "
        "WHERE device_key = CAST(:dk AS uuid) AND top_latin IS NOT NULL"),
        {"dk": str(device_key)})).scalars().all()
    syn = await synonym_map(db)
    keys = {canonical_key(_latin_key(n), syn) for n in rows if n}
    return len({k for k in keys if k})


async def _best_streak(db, device_key) -> int:
    """Самая длинная серия дней подряд со снимком. Именно ЛУЧШАЯ, а не текущая:
    отобрать уже выданный значок, потому что человек пропустил день, — обидно и
    неправильно; серия остаётся достижением."""
    days = (await db.execute(text(
        "SELECT DISTINCT (COALESCE(captured_at, created_at) AT TIME ZONE 'UTC')::date d "
        "FROM identifications WHERE device_key = CAST(:dk AS uuid) ORDER BY d"),
        {"dk": str(device_key)})).scalars().all()
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if (prev is not None and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d
    return best


async def _invited_count(db, device_key) -> int:
    """Сколько приведённых людей действительно пользуются приложением: не меньше
    трёх определений в разные дни. Клик по ссылке наградой не считается."""
    return (await db.execute(text("""
        SELECT count(*) FROM quest_devices d
        WHERE d.invited_by = CAST(:dk AS uuid)
          AND (SELECT count(*) FROM identifications i WHERE i.device_key = d.device_key)
              >= :min_shots
          AND (SELECT count(DISTINCT (COALESCE(i.captured_at, i.created_at)
                                      AT TIME ZONE 'UTC')::date)
               FROM identifications i WHERE i.device_key = d.device_key) >= :min_days"""),
        {"dk": str(device_key), "min_shots": _INVITE_MIN_SHOTS,
         "min_days": _INVITE_MIN_DAYS})).scalar() or 0


async def _friends_count(db, device_key) -> int:
    """Взаимные подписки — «друзья» (см. public_profile: подписка ≠ подписчик ≠ друг)."""
    return (await db.execute(text("""
        SELECT count(*) FROM quest_follows f
        JOIN quest_devices me ON me.device_key = f.follower_key
        JOIN quest_devices them ON them.handle = f.followee_handle
        JOIN quest_follows back ON back.follower_key = them.device_key
                               AND back.followee_handle = me.handle
        WHERE f.follower_key = CAST(:dk AS uuid)"""),
        {"dk": str(device_key)})).scalar() or 0


async def accept_invite(db: AsyncSession, device_key: str, code: str) -> dict:
    """«Меня пригласил(а)» — привязать устройство к пригласившему по его handle.

    Проверки: себя не пригласишь, дважды не пригласишь, и только в первые
    _INVITE_WINDOW_DAYS дней жизни устройства — иначе код вводили бы через полгода
    ради значка знакомому."""
    dk = _try_uuid(device_key)
    if dk is None:
        return {"error": "device not registered"}
    me = await db.get(Device, dk)
    if me is None:
        return {"error": "device not registered"}
    if me.invited_by is not None:
        return {"error": "приглашение уже отмечено"}
    host = (await db.execute(select(Device).where(
        Device.handle == (code or "").strip().lower()))).scalar_one_or_none()
    if host is None:
        return {"error": "такого кода нет"}
    if host.device_key == dk:
        return {"error": "нельзя пригласить самого себя"}
    age_days = (datetime.now(timezone.utc) - me.created_at).days if me.created_at else 0
    if age_days > _INVITE_WINDOW_DAYS:
        return {"error": "код можно ввести только в первые дни после установки"}
    me.invited_by = host.device_key
    me.invited_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok", "host_handle": host.handle,
            "host_nick": host.nickname or auto_nick(host.device_key)}


async def meta_progress(db: AsyncSession, device_key: str) -> dict:
    """Прогресс по наградам без географии. Форма ответа повторяет badge_progress,
    чтобы клиент рисовал их той же лестницей."""
    dk = _try_uuid(device_key)
    if dk is None:
        return {"items": []}
    items = []
    counters = {
        "collection": _collection_size,
        "streak": _best_streak,
        "invites": _invited_count,
        "friends": _friends_count,
    }
    for kind in _META_LADDERS:
        matched = await counters[kind](db, dk)
        badge_id = f"meta:{kind}"
        tiers = _meta_tiers(kind)
        issued = await _issued_tiers(db, badge_id, dk)
        earned = [t for t in tiers if t["need"] <= matched]
        not_earned = [t for t in tiers if t["need"] > matched]
        items.append({
            "kind": kind, "badge_id": badge_id,
            "title": _META_LADDERS[kind]["title"], "unit": _META_LADDERS[kind]["unit"],
            "matched": matched, "tiers": tiers,
            "current_tier": max((t["tier"] for t in earned), default=0),
            "claimable_tier": max((t["tier"] for t in earned if t["tier"] not in issued),
                                  default=None),
            "next_need": not_earned[0]["need"] if not_earned else None,
        })
    return {"items": items}


async def claim_meta_badge(db: AsyncSession, device_key: str, kind: str) -> dict:
    """Выдать заработанные, но не забранные ярусы награды без географии."""
    if kind not in _META_LADDERS:
        return {"error": "unknown badge kind"}
    dk = _try_uuid(device_key)
    if dk is None:
        return {"error": "device not registered"}
    prog = next(i for i in (await meta_progress(db, str(dk)))["items"] if i["kind"] == kind)
    badge_id = prog["badge_id"]
    issued = await _issued_tiers(db, badge_id, dk)
    unissued = [t for t in prog["tiers"] if t["need"] <= prog["matched"] and t["tier"] not in issued]
    if not unissued:
        return {**prog, "issued": False,
                "reason": "already" if issued else "below_first_tier"}
    granted = []
    for tr in unissued:
        n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
            QuestIssuedBadge.badge_id == badge_id,
            QuestIssuedBadge.tier == tr["tier"]))).scalar() or 0
        db.add(QuestIssuedBadge(badge_id=badge_id, device_key=dk, tier=tr["tier"],
                                points=tr["points"], ordinal=n + 1, window_closed=False))
        granted.append({**tr, "ordinal": n + 1})
    await db.commit()
    top = granted[-1]
    prog = next(i for i in (await meta_progress(db, str(dk)))["items"] if i["kind"] == kind)
    return {**prog, "issued": True, "tier": top["tier"], "name": top["name"],
            "ordinal": top["ordinal"], "points": top["points"], "granted": granted}


async def claimable_badges(db: AsyncSession, device_key: str,
                           include_biotopes: bool = True) -> dict:
    """Все ЗАРАБОТАННЫЕ, но не забранные значки девайса за текущее окно — одним
    запросом, для баннера «забери значок» на главной. До этого клиенту пришлось бы
    поллить badge_progress по каждому месту отдельно, поэтому заработанные значки
    молча висели незабранными (кейс Северо-Курильска: новичок month-08 ждал с 8-го
    числа). Места-кандидаты = только те, где девайс реально стрелял в этом окне."""
    dk = _try_uuid(device_key)
    if dk is None:
        return {"count": 0, "items": []}
    label = _current_window()
    yr = date.today().year
    # Cumulative model: candidate places = anywhere the device EVER shot (capped).
    rows = (await db.execute(text("""
        SELECT DISTINCT p.id::text, p.name
        FROM identifications i
        JOIN quest_places p ON p.geom IS NOT NULL
         AND ST_Contains(p.geom, ST_SetSRID(ST_MakePoint(i.lng, i.lat), 4326))
        WHERE i.device_key = CAST(:dk AS uuid) AND i.lat IS NOT NULL
        LIMIT 12"""), {"dk": str(dk)})).all()
    items = []
    for pid, name in rows:
        prog = await badge_progress(db, str(dk), pid, label, yr)
        if prog.get("claimable_tier"):
            items.append({
                "kind": "place", "place_id": pid, "place": name,
                "window": label, "year": yr,
                "claimable_tier": prog["claimable_tier"],
                "tier_name": _TIER_NAMES.get(prog["claimable_tier"], ""),
                "matched": prog["matched"], "target": prog["target"],
                "next_need": prog["next_need"],
            })
    # награды без географии — тем, кто снимает, но не ходит по квест-местам
    try:
        for m in (await meta_progress(db, str(dk)))["items"]:
            if m.get("claimable_tier"):
                items.append({
                    "kind": m["kind"], "title": m["title"], "unit": m["unit"],
                    "claimable_tier": m["claimable_tier"],
                    "tier_name": _TIER_NAMES.get(m["claimable_tier"], ""),
                    "matched": m["matched"], "next_need": m["next_need"],
                })
    except Exception:
        pass
    if include_biotopes:
        try:
            matches = await _device_biotope_matches(db, str(dk))
            for bio in matches:
                prog = await biotope_progress(db, str(dk), bio, _matches=matches)
                if prog.get("claimable_tier"):
                    items.append({
                        "kind": "biotope", "biotope": bio,
                        "claimable_tier": prog["claimable_tier"],
                        "tier_name": _TIER_NAMES.get(prog["claimable_tier"], ""),
                        "matched": prog["matched"],
                    })
        except Exception:
            pass   # биотоп-скан — бонус; его сбой не должен прятать place-значки
    return {"window": label, "year": yr, "count": len(items), "items": items}


async def quest_credit_for_shot(db: AsyncSession, device_key: str, lat: float, lng: float,
                                cands, top_latin, top_score) -> dict | None:
    """Кредит квесту за ТОЛЬКО ЧТО сделанный снимок — для экрана результата
    определения («зачтено в квест X, 4 из 9»). Связывает момент съёмки с игрой:
    сейчас 5 из 6 определяющих девайсов вообще не открывают вкладку квестов.
    Возвращает None, когда снимок вне квест-мест / без гео / нет набора — клиент
    просто не рисует блок. Вызывается ПОСЛЕ архивации снимка, так что
    badge_progress уже учитывает и его."""
    dk = _try_uuid(device_key) if device_key else None
    if dk is None or lat is None or lng is None:
        return None
    label = _current_window()
    yr = date.today().year
    rows = (await db.execute(text("""
        SELECT id::text, name FROM quest_places
        WHERE geom IS NOT NULL
          AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326))
        ORDER BY ST_Area(geom) ASC LIMIT 3"""), {"lat": lat, "lng": lng})).all()
    syn = await synonym_map(db)
    for pid, name in rows:                       # smallest containing place with a pool wins
        pool, _ = await _pool_for(db, pid)       # cumulative: credit vs the FULL pool
        if not pool:
            continue
        ex, sf = _credit_keys(cands, top_latin, top_score, pool, syn)
        prog = await badge_progress(db, str(dk), pid, label, yr)
        if "error" in prog:
            continue
        return {"place_id": pid, "place": name, "window": label, "year": yr,
                "this_shot": sorted(ex | sf), "matched": prog["matched"],
                "target": prog["target"], "next_need": prog["next_need"],
                "current_tier": prog["current_tier"],
                "claimable_tier": prog["claimable_tier"]}
    return None


async def _badge_issued(db, device_key, place_id, window, year) -> bool:
    """Cumulative model: one badge per place (badge_id = '{place_id}:all');
    window/year kept in the signature for caller compatibility, ignored."""
    if not device_key:
        return False
    n = (await db.execute(select(func.count()).select_from(QuestIssuedBadge).where(
        QuestIssuedBadge.badge_id == f"{place_id}:all",
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
          -- личное место — только своему владельцу: чужая дача не должна быть пином
          AND (p.kind <> 'personal' OR p.owner_key = CAST(:dk AS uuid))
          AND ST_DWithin(ST_Centroid(p.geom)::geography,
                         ST_SetSRID(ST_MakePoint(:lng,:lat),4326)::geography, :rad)
        ORDER BY dist LIMIT :lim
    """), {"lat": lat, "lng": lng, "win": win, "rad": radius_km * 1000.0, "lim": limit,
              "dk": str(_try_uuid(device_key)) if _try_uuid(device_key) else None})).all()

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


async def places_in_bounds(db: AsyncSession, min_lat: float, min_lng: float,
                           max_lat: float, max_lng: float,
                           window: str | None = None, limit: int = 300,
                           device_key: str | None = None) -> dict:
    """Quest places whose centroid falls inside the map viewport (bbox) and have a
    species-set for the window — powers the pan-to-search map (so the user can scroll
    the map anywhere and see quests there, or pan toward a distant one to travel to).
    When ``device_key`` is given, each place carries this device's LIGHT status so the
    list can mark «✓ пройден» / «в процессе» (item 3): `top_tier` = highest issued tier
    here (0 = none), `started` = has any geotagged find inside the polygon. Status is
    two cheap aggregate queries — NOT 300× badge_progress."""
    win = window or _current_window()
    # LATERAL fallback: prefer the current window's set, else the most recently computed
    # one — so a place never drops off the map at a window rollover (and old custom quests
    # persist). `eff_win` is the window actually used, echoed back for the client's calls.
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.kind,
               ST_Y(ST_Centroid(p.geom)) AS clat, ST_X(ST_Centroid(p.geom)) AS clng,
               ps.target, COALESCE(array_length(ps.species_set,1),0) AS set_size, ps.window_label AS eff_win
        FROM quest_places p
        JOIN LATERAL (
            SELECT target, species_set, window_label FROM quest_place_sets s
            WHERE s.place_id = p.id
            ORDER BY (s.window_label = :win) DESC, s.computed_at DESC
            LIMIT 1
        ) ps ON true
        WHERE p.geom IS NOT NULL
          -- личное место рисуем только его владельцу (см. ensure_personal_place)
          AND (p.kind <> 'personal' OR p.owner_key = CAST(:dk AS uuid))
          AND ST_Centroid(p.geom) && ST_MakeEnvelope(:minlng,:minlat,:maxlng,:maxlat,4326)
        LIMIT :lim
    """), {"win": win, "minlng": min_lng, "minlat": min_lat,
           "maxlng": max_lng, "maxlat": max_lat, "lim": limit,
           "dk": str(_try_uuid(device_key)) if _try_uuid(device_key) else None})).all()
    ids = [str(r[0]) for r in rows]
    top_tier: dict[str, int] = {}     # place_id -> highest issued tier
    started: set[str] = set()         # place_id -> has a geotagged find inside
    dk = _try_uuid(device_key) if device_key else None
    if dk is not None and ids:
        # Passed: highest issued tier per place (badge_id = "<place_id>:<window>:<year>").
        brows = (await db.execute(select(QuestIssuedBadge.badge_id, QuestIssuedBadge.tier)
                                  .where(QuestIssuedBadge.device_key == dk))).all()
        for bid, tier in brows:
            pid = bid.split(":")[0]
            if pid in set(ids):
                top_tier[pid] = max(top_tier.get(pid, 0), tier)
        # Started: any geotagged identification of this device that lands in a visible
        # polygon (one spatial join over the device's bounded finds). A cheap proxy for
        # «начал проходить» — exact matched-count stays on the quest page.
        srows = (await db.execute(text("""
            SELECT qp.id::text
            FROM identifications i
            JOIN quest_places qp ON qp.id = ANY(CAST(:ids AS uuid[]))
              AND ST_Contains(qp.geom, ST_SetSRID(ST_MakePoint(i.lng, i.lat), 4326))
            WHERE i.device_key = CAST(:dk AS uuid) AND i.lat IS NOT NULL AND i.lng IS NOT NULL
            GROUP BY qp.id
        """), {"ids": ids, "dk": str(dk)})).all()
        started = {r[0] for r in srows}
    places = [{
        "id": str(pid), "name": name, "kind": kind,
        "lat": clat, "lng": clng, "distance_km": None,
        "window": eff_win, "set_size": set_size, "target": target,
        "matched": 0,
        "badge_issued": top_tier.get(str(pid), 0) > 0,
        "top_tier": top_tier.get(str(pid), 0),
        "top_tier_name": _TIER_NAMES.get(top_tier.get(str(pid), 0)) if top_tier.get(str(pid), 0) else None,
        "started": str(pid) in started,
    } for pid, name, kind, clat, clng, target, set_size, eff_win in rows]
    return {"places": places}


async def place_set(db: AsyncSession, place_id: str, window: str | None = None,
                    device_key=None, year: int | None = None,
                    biotope: str | None = None) -> dict:
    """«What to look for here» — species cards from the SAVED set (no live iNat).
    Names/photos come from species_meta (saved at compute) with a corpus fallback;
    plant_id via the latin-key bridge; found = this device identified it in the
    polygon×window. ``biotope`` filters to species of that habitat (GPS→biotope
    half-b: join the place's expected species with plant_biotopes)."""
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
    syn = await synonym_map(db)
    items = []
    for m in meta:
        key = m["key"]
        p = plant_map.get(key)
        latin = m.get("latin") or (key[:1].upper() + key[1:])
        items.append({
            "latin_key": key,
            "name": m.get("name") or (p.name if p else None) or latin,
            "latin": latin,
            # Canonical accepted-name key — lets the client verdict say «same species»
            # across synonyms / duplicate cards (a Stachys shot vs a Betonica set card).
            "species_key": canonical_key(key, syn),
            "inat_photo": m.get("photo") or (p.photo_url if p else None),
            "plant_id": str(p.id) if p else None,
            "found": key in found_keys,
            # custom quests tag each species confirmed/expected (RFC-custom-quests); named
            # places have no flag → default confirmed.
            "confidence": m.get("confidence", "confirmed"),
        })
    # GPS→biotope filter: keep only species of the requested habitat (those whose
    # corpus card is tagged with `biotope` in plant_biotopes).
    if biotope:
        pids = [i["plant_id"] for i in items if i["plant_id"]]
        keep: set[str] = set()
        if pids:
            keep = {r[0] for r in (await db.execute(text(
                "SELECT DISTINCT plant_id::text FROM plant_biotopes "
                "WHERE plant_id = ANY(cast(:ids as uuid[])) AND biotope = :b"),
                {"ids": pids, "b": biotope})).all()}
        items = [i for i in items if i["plant_id"] in keep]
    return {"place": {"id": str(place_id), "name": place.name if place else None,
                      "window": win, "set_size": len(meta), "target": ps.target,
                      "matched": matched, "badge_issued": await _badge_issued(db, device_key, place_id, win, yr)},
            "biotope": biotope, "items": items}


async def place_biotopes(db: AsyncSession, place_id: str, window: str | None = None) -> dict:
    """«Что искать здесь, по среде» — the place's landcover biotopes (GPS→biotope
    half-b) with how many of its expected species belong to each. Tap a biotope →
    ``place/{id}/set?biotope=<key>`` for the filtered cards."""
    from app.services.biotope import BIOTOPE_GROUP
    win = window or _current_window()
    pbs = [r[0] for r in (await db.execute(text(
        "SELECT DISTINCT biotope FROM quest_place_biotopes "
        "WHERE place_id = :p AND biotope IS NOT NULL"), {"p": place_id})).all()]
    counts: dict[str, int] = {}
    if pbs:
        ps = await _set_for(db, place_id, win)
        if ps:
            meta = ps.species_meta or [{"key": k} for k in (ps.species_set or [])]
            plant_map = await resolve_latin_to_plants(db, [m["key"] for m in meta])
            pids = [str(p.id) for p in plant_map.values() if p]
            if pids:
                counts = {b: n for b, n in (await db.execute(text(
                    "SELECT biotope, count(DISTINCT plant_id) FROM plant_biotopes "
                    "WHERE plant_id = ANY(cast(:ids as uuid[])) AND biotope = ANY(:bs) "
                    "GROUP BY biotope"), {"ids": pids, "bs": pbs})).all()}
    return {"place_id": str(place_id), "window": win,
            "biotopes": [{"key": b, "group": BIOTOPE_GROUP.get(b, "прочее"),
                          "species_count": counts.get(b, 0)} for b in sorted(pbs)]}


# --------------------------------------------------- Phase 6: leaderboard (v1)

async def leaderboard(db: AsyncSession, device_key=None, limit: int = 20,
                      scope: str = "global", place_id: str | None = None,
                      window: str | None = None, year: int | None = None) -> dict:
    """All-time leaderboard. score = Σ over each place×season badge of the points
    of the HIGHEST tier reached (NOT the sum of all tiers — that would double-count
    the ladder). `badges` = distinct place×season badges held. Server-counted
    (client never sends a score). Dense rank by score desc, tie-break earliest badge.
    `me` returned even when outside the top.

    `scope` narrows the board (badge_id is `{place_id}:{window}:{year}`):
      • global — all badges (default, back-compatible)
      • place  — only badges at `place_id`   (all its windows/years)
      • season — only badges in `window`×`year` (across all places)."""
    pat = None
    if scope == "place" and place_id:
        pat = f"{place_id}:%"
    elif scope == "season" and window:
        pat = f"%:{window}:{year or date.today().year}"
    # Build the filter conditionally — never bind a NULL param (asyncpg can't infer
    # the type of `:pat` in `:pat IS NULL` → "could not determine data type"). The
    # global path stays byte-identical to the original query (no WHERE, no params).
    where = "WHERE badge_id LIKE :pat" if pat is not None else ""
    rows = (await db.execute(text(f"""
        SELECT device_key, SUM(maxpts) AS score, COUNT(*) AS badges, MIN(first_at) AS first_at
        FROM (SELECT device_key, badge_id,
                     MAX(COALESCE(points, 0)) AS maxpts, MIN(issued_at) AS first_at
              FROM quest_issued_badges
              {where}
              GROUP BY device_key, badge_id) z
        GROUP BY device_key
    """), ({"pat": pat} if pat is not None else {}))).all()
    ranked = sorted(rows, key=lambda r: (-r.score, r.first_at))

    nicks: dict = {}
    handles: dict = {}
    blocked: set = set()
    if ranked:
        devs = (await db.execute(select(
            Device.device_key, Device.nickname, Device.handle, Device.blocked).where(
            Device.device_key.in_([r.device_key for r in ranked])))).all()
        for dk_, nk_, hd_, bl_ in devs:
            nicks[dk_] = nk_
            handles[dk_] = hd_
            if bl_:
                blocked.add(dk_)
    ranked = [r for r in ranked if r.device_key not in blocked]   # hide blocked

    # dense rank (after excluding blocked)
    rank_of: dict = {}
    rank, prev = 0, None
    for r in ranked:
        if r.score != prev:
            rank += 1
            prev = r.score
        rank_of[r.device_key] = rank

    def entry(r):
        return {"rank": rank_of[r.device_key],
                "handle": handles.get(r.device_key) or auto_handle(r.device_key),
                "nick": nicks.get(r.device_key) or auto_nick(r.device_key),
                "score": int(r.score or 0), "badges": r.badges}

    top = [entry(r) for r in ranked[:limit]]
    me = None
    if device_key:
        dk = uuid_or(device_key)
        hit = next((r for r in ranked if r.device_key == dk), None)
        me = entry(hit) if hit else {
            "rank": None, "handle": auto_handle(dk), "nick": auto_nick(dk), "score": 0, "badges": 0}
    return {"me": me, "top": top}


def uuid_or(v):
    import uuid as _u
    return v if isinstance(v, _u.UUID) else _u.UUID(str(v))


# --------------------------------------------------- Phase 7: public social read
#
# Read-only surfaces for the landing site (botanik.fun): a public «паспорт
# натуралиста», a recent-badges activity feed, and leaderboard scoping (above).
# GEOPRIVACY: these expose the place NAME only — never coordinates.

# Client-side personal level ladder (mirrors Quest.kt naturalistLevel): species
# thresholds → title. Computed from this device's distinct identifications.
_LEVEL_AT = [1, 5, 15, 40, 100]
_LEVEL_TITLE = ["новичок", "любитель", "знаток", "мастер", "легенда"]


def _level_for(species: int) -> dict:
    idx = 0
    for i, a in enumerate(_LEVEL_AT):
        if species >= a:
            idx = i
    return {"n": idx + 1, "title": _LEVEL_TITLE[idx], "species": species}


async def _place_names(db, place_ids) -> dict:
    """{place_id(str): name} for the given place ids (the badge_id first segment)."""
    ids = []
    for p in {pid for pid in place_ids if pid}:
        try:
            ids.append(uuid_or(p))
        except (ValueError, AttributeError):
            pass
    if not ids:
        return {}
    rows = (await db.execute(select(QuestPlace.id, QuestPlace.name).where(
        QuestPlace.id.in_(ids)))).all()
    return {str(i): n for i, n in rows}


async def _species_count(db, device_key) -> int:
    """Distinct species this device identified (genus+species, like the client)."""
    lats = (await db.execute(select(Identification.top_latin).where(
        Identification.device_key == uuid_or(device_key),
        Identification.top_latin.isnot(None)))).scalars().all()
    seen = set()
    for s in lats:
        norm = " ".join((s or "").strip().lower().split()[:2])
        if norm:
            seen.add(norm)
    return len(seen)


def _try_uuid(s):
    import uuid as _u
    try:
        return _u.UUID(str(s))
    except (ValueError, AttributeError, TypeError):
        return None


async def resolve_subject(db, id_str):
    """Public id → device_key. Accepts a `handle` (preferred) OR a legacy device_key
    UUID (share links still in the wild). None for an unknown handle."""
    u = _try_uuid(id_str)
    if u is not None:
        return u
    return (await db.execute(select(Device.device_key).where(
        Device.handle == id_str))).scalar_one_or_none()


async def public_profile(db: AsyncSession, id_str, viewer_device_key=None) -> dict | None:
    """Public «паспорт» by handle (or legacy device_key). nick/avatar/level/score/rank
    + badge shelf (place NAMES, no coordinates). device_key is NEVER returned. None →
    unknown handle or blocked (router → 404). `is_following` set when a viewer is given."""
    dk = await resolve_subject(db, id_str)
    if dk is None:
        return None
    dev = await db.get(Device, dk)
    if dev and dev.blocked:
        return None
    handle = (dev.handle if dev else None) or auto_handle(dk)
    species = await _species_count(db, dk)
    shelf = await badge_shelf(db, str(dk))
    names = await _place_names(db, [b.get("place_id") for b in shelf])
    for b in shelf:
        b["place"] = names.get(b.get("place_id"))
    board = await leaderboard(db, device_key=str(dk), limit=1)
    me = board.get("me") or {}
    # Подписки/подписчики — РАЗНЫЕ вещи (решение Олега 2026-08-25): «подписки» =
    # кого читает он, «подписчики» = кто читает его, «друг» = ВЗАИМНАЯ подписка.
    # Раньше в UI и то и другое звалось «друзья», хотя связь односторонняя.
    followers = (await db.execute(select(func.count()).select_from(QuestFollow).where(
        QuestFollow.followee_handle == handle))).scalar() or 0
    following_n = (await db.execute(select(func.count()).select_from(QuestFollow).where(
        QuestFollow.follower_key == dk))).scalar() or 0
    is_following = None      # смотрящий подписан на него
    follows_me = None        # он подписан на смотрящего
    is_mutual = None
    if viewer_device_key:
        vk = _try_uuid(viewer_device_key)
        if vk is not None:
            n = (await db.execute(select(func.count()).select_from(QuestFollow).where(
                QuestFollow.follower_key == vk, QuestFollow.followee_handle == handle))).scalar() or 0
            is_following = n > 0
            vdev = await db.get(Device, vk)
            vhandle = (vdev.handle if vdev else None) or auto_handle(vk)
            back = (await db.execute(select(func.count()).select_from(QuestFollow).where(
                QuestFollow.follower_key == dk, QuestFollow.followee_handle == vhandle))).scalar() or 0
            follows_me = back > 0
            is_mutual = bool(is_following and follows_me)
    return {
        "handle": handle,
        "nick": (dev.nickname if dev else None) or auto_nick(dk),
        "avatar": dev.avatar if dev else None,
        "level": _level_for(species),
        "score": me.get("score", 0),
        "rank": me.get("rank"),
        "badges": shelf,
        "is_following": is_following,
        "follows_me": follows_me,
        "is_mutual": is_mutual,
        "followers": followers,
        "following": following_n,
    }


# --------------------------------------------------- Phase 8: follows + feed

async def ensure_handle(db, device_key) -> str:
    """Return the device's handle, generating + storing it on first need (also serves
    backfill). Deterministic, so concurrent calls converge."""
    dk = uuid_or(device_key)
    dev = await db.get(Device, dk)
    h = auto_handle(dk)
    if dev and not dev.handle:
        dev.handle = h
        await db.commit()
    return (dev.handle if dev else None) or h


async def follow(db, device_key, target_handle: str) -> dict:
    vk = uuid_or(device_key)
    target = await resolve_subject(db, target_handle)
    if target is None:
        return {"error": "unknown handle"}
    if target == vk:
        return {"error": "cannot follow yourself"}
    tdev = await db.get(Device, target)
    if tdev and tdev.blocked:
        return {"error": "unavailable"}
    # store followee as its canonical handle
    th = (tdev.handle if tdev else None) or auto_handle(target)
    res = await db.execute(pg_insert(QuestFollow).values(
        follower_key=vk, followee_handle=th).on_conflict_do_nothing(
        index_elements=["follower_key", "followee_handle"]))
    await db.commit()
    # Взаимность: подписан ли ОН на меня (тогда это «друг», а не просто подписка).
    vdev = await db.get(Device, vk)
    vhandle = (vdev.handle if vdev else None) or auto_handle(vk)
    back = (await db.execute(select(func.count()).select_from(QuestFollow).where(
        QuestFollow.follower_key == target, QuestFollow.followee_handle == vhandle))).scalar() or 0
    mutual = back > 0
    # Уведомление подписчику — только на НОВУЮ подписку (rowcount 0 = была раньше).
    # Без него подписка уходила в пустоту: человек никак не узнавал об интересе.
    if res.rowcount:
        try:
            from app.services.push import send_to_device
            nick = (vdev.nickname if vdev else None) or auto_nick(vk)
            await send_to_device(
                db, str(target),
                "Взаимно!" if mutual else "На тебя подписались",
                (f"{nick} тоже подписан(а) на тебя — теперь вы друзья."
                 if mutual else f"{nick} следит за твоими находками."),
                {"kind": "new_follower", "handle": vhandle},
            )
        except Exception as e:
            logger.info(f"follow push skipped: {type(e).__name__}: {e}")
    return {"status": "ok", "following": True, "handle": th, "mutual": mutual}


async def unfollow(db, device_key, target_handle: str) -> dict:
    vk = uuid_or(device_key)
    await db.execute(QuestFollow.__table__.delete().where(
        (QuestFollow.follower_key == vk) & (QuestFollow.followee_handle == target_handle)))
    await db.commit()
    return {"status": "ok", "following": False}


async def _actors_for(db, handles: list[str], public_only: bool):
    """{device_key: {handle,nick,avatar}} for the given handles, dropping blocked
    (and, when public_only, activity_public=false)."""
    if not handles:
        return {}
    q = select(Device.device_key, Device.handle, Device.nickname, Device.avatar).where(
        Device.handle.in_(handles), Device.blocked.is_(False))
    if public_only:
        q = q.where(Device.activity_public.is_(True))
    rows = (await db.execute(q)).all()
    return {dk: {"handle": h, "nick": nk or auto_nick(dk), "avatar": av} for dk, h, nk, av in rows}


async def following(db, device_key) -> dict:
    vk = uuid_or(device_key)
    handles = (await db.execute(select(QuestFollow.followee_handle).where(
        QuestFollow.follower_key == vk))).scalars().all()
    actors = await _actors_for(db, handles, public_only=False)
    dev = await db.get(Device, vk)
    my_handle = (dev.handle if dev else None) or auto_handle(vk)
    # Кто из них подписан на МЕНЯ → это «друзья» (взаимные), остальные — подписки.
    back = set()
    if actors:
        rows = (await db.execute(select(QuestFollow.follower_key).where(
            QuestFollow.followee_handle == my_handle,
            QuestFollow.follower_key.in_(list(actors.keys()))))).scalars().all()
        back = set(rows)
    out = [{**a, "mutual": dk in back} for dk, a in actors.items()]
    return {"following": out}


async def feed(db, device_key, limit: int = 30, scope: str = "following") -> dict:
    """Merged recent activity: in-corpus identifications + badges. No coordinates;
    excludes blocked + activity_public=false.

    scope="following" — только те, на кого подписан (как было);
    scope="ether" — «Эфир» (идея Олега 2026-08-24): ВСЕ публичные находки сообщества.
    При 9 подписках на всё приложение лента подписок мертва — эфир живёт сразу.
    Место события — ПРИБЛИЗИТЕЛЬНОЕ: имя известного квест-места, никогда координаты."""
    if scope == "ether":
        rows = (await db.execute(select(
            Device.device_key, Device.handle, Device.nickname, Device.avatar).where(
            Device.blocked.is_(False), Device.activity_public.is_(True)))).all()
        actors = {dk: {"handle": h or auto_handle(dk), "nick": nk or auto_nick(dk),
                       "avatar": av} for dk, h, nk, av in rows}
    else:
        vk = uuid_or(device_key)
        handles = (await db.execute(select(QuestFollow.followee_handle).where(
            QuestFollow.follower_key == vk))).scalars().all()
        actors = await _actors_for(db, handles, public_only=True)
    if not actors:
        return {"events": []}
    keys = list(actors.keys())

    ids = (await db.execute(select(
        Identification.id, Identification.device_key, Identification.matched_plant_id,
        Identification.created_at).where(
        Identification.device_key.in_(keys), Identification.matched_plant_id.isnot(None))
        .order_by(Identification.created_at.desc()).limit(limit))).all()
    # Приблизительное «где»: имя самого маленького известного места, накрывающего точку
    # (парк/лес/кастом). Точных координат в ленте НЕТ никогда.
    coarse: dict = {}
    iid_list = [str(i) for i, _, _, _ in ids]
    if iid_list:
        # kind='custom' исключён: кастом-квесты создают на дачах, и повторяющийся
        # топоним при нике («… · Курниково») по совокупности выдаёт деревню конкретного
        # человека. Публичные OSM-места (парки/леса) именем светиться могут — они и
        # есть публичные.
        crows = (await db.execute(text("""
            SELECT i.id::text,
                   (SELECT p.name FROM quest_places p
                     WHERE p.geom IS NOT NULL AND i.lat IS NOT NULL
                       AND p.kind NOT IN ('custom', 'personal')
                       AND ST_Contains(p.geom, ST_SetSRID(ST_MakePoint(i.lng, i.lat), 4326))
                     ORDER BY ST_Area(p.geom) LIMIT 1)
            FROM identifications i WHERE i.id = ANY(CAST(:ids AS uuid[]))"""),
            {"ids": iid_list})).all()
        coarse = {rid: nm for rid, nm in crows if nm}
    plant_ids = list({pid for _, _, pid, _ in ids if pid})
    plants: dict = {}
    if plant_ids:
        prows = (await db.execute(select(
            Plant.id, Plant.name, Plant.name_modern, Plant.photo_url).where(
            Plant.id.in_(plant_ids)))).all()
        plants = {pid: {"id": str(pid), "name": (nm or nmm or "растение"),
                        "name_modern": nmm, "photo": ph}
                  for pid, nm, nmm, ph in prows}

    badges = (await db.execute(select(
        QuestIssuedBadge.device_key, QuestIssuedBadge.badge_id, QuestIssuedBadge.tier,
        QuestIssuedBadge.ordinal, QuestIssuedBadge.issued_at).where(
        QuestIssuedBadge.device_key.in_(keys))
        .order_by(QuestIssuedBadge.issued_at.desc()).limit(limit))).all()
    place_names = await _place_names(db, [b.badge_id.split(":")[0] for b in badges])

    events = []
    for iid, dk, pid, at in ids:
        pl = plants.get(pid)
        if not pl:
            continue
        events.append({"type": "id", "actor": actors[dk],
                       "plant": {"id": pl["id"], "name": pl.get("name_modern") or pl["name"], "photo": pl["photo"]},
                       "place": coarse.get(str(iid)),
                       "at": at.isoformat()})
    for b in badges:
        parts = b.badge_id.split(":")
        events.append({"type": "badge", "actor": actors[b.device_key],
                       "place": place_names.get(parts[0] if parts else None),
                       "tier": b.tier, "name": _TIER_NAMES.get(b.tier, ""),
                       "ordinal": b.ordinal, "at": b.issued_at.isoformat()})
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"events": events[:limit]}


async def set_activity_public(db, device_key, value: bool) -> dict:
    dev = await db.get(Device, uuid_or(device_key))
    if not dev:
        return {"error": "device not registered"}
    dev.activity_public = bool(value)
    await db.commit()
    return {"status": "ok", "activity_public": dev.activity_public}


async def recent_badges(db: AsyncSession, limit: int = 20, place_id: str | None = None) -> dict:
    """Newest issued badges (social-proof feed) — nick + place name + tier, no geo."""
    q = (select(QuestIssuedBadge)
         .order_by(QuestIssuedBadge.issued_at.desc()).limit(limit))
    if place_id:
        q = q.where(QuestIssuedBadge.badge_id.like(f"{place_id}:%"))
    rows = (await db.execute(q)).scalars().all()
    dks = list({r.device_key for r in rows})
    nicks: dict = {}
    if dks:
        devs = (await db.execute(select(Device.device_key, Device.nickname).where(
            Device.device_key.in_(dks)))).all()
        nicks = {dk: nk for dk, nk in devs}
    names = await _place_names(db, [r.badge_id.split(":")[0] for r in rows])
    feed = []
    for r in rows:
        parts = r.badge_id.split(":")
        pid = parts[0] if parts else None
        feed.append({
            "nick": nicks.get(r.device_key) or auto_nick(r.device_key),
            "tier": r.tier, "name": _TIER_NAMES.get(r.tier, ""),
            "place": names.get(pid), "place_id": pid,
            "window": parts[1] if len(parts) > 1 else None,
            "year": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
            "ordinal": r.ordinal, "issued_at": r.issued_at.isoformat(),
        })
    return {"badges": feed}
