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
from datetime import date

import httpx
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inaturalist import INAT_BASE, _HEADERS
from app.services.plant_matching import resolve_latin_to_plants, _latin_key
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
_NICK_NOUN = ["Бегемот", "Лис", "Барсук", "Филин", "Ёж", "Зубр", "Олень", "Бобр", "Аист",
              "Сокол", "Рысь", "Глухарь", "Хорёк", "Журавль", "Кабан", "Тетерев", "Енот",
              "Выдра", "Дрозд", "Шмель"]


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
_NEAR_TIE = 0.85                # badge credit if a set species scores ≥85% of the top
                                # candidate (sibling-species ranking is near-random)


def _credit_keys(cands, top_latin, top_score, target: set, target_genera: set) -> tuple[set, set]:
    """Lenient Q1 match (HANDOFF-identify-improvements) of ONE identification's
    candidate list against a `target` set of latin_keys. Returns (exact, soft):
    `exact` = a target species that is the top candidate OR a near-tie (score ≥ 85%
    of top); `soft` = genus-level «almost» (top candidate's genus = a target member's
    genus). Single source of truth for badge / found-state / biotope-mastery credit."""
    exact, soft = set(), set()
    cands = cands or []
    if not cands:
        k = _latin_key(top_latin)
        if k in target:
            exact.add(k)
        return exact, soft
    ts = top_score or max((c.get("score") or 0) for c in cands) or 1.0
    for c in cands:
        ck = _latin_key(c.get("latin"))
        if ck in target and (c.get("score") or 0) >= ts * _NEAR_TIE:
            exact.add(ck)
    top_g = (_latin_key((cands[0] or {}).get("latin")) or " ").split(" ")[0]
    if top_g and top_g in target_genera:
        soft |= {k for k in target if k.split(" ")[0] == top_g}
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

    items, seen = [], set()
    for s in sorted(recognizable, key=_tier):   # stable → iNat-frequency order kept within tier
        k = _latin_key(s["latin"])
        if k in seen:
            continue
        seen.add(k)
        p = plant_map.get(s["latin"])
        items.append({
            "latin_key": k, "name": s.get("name_ru") or s["latin"], "latin": s["latin"],
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


# ----------------------------------------------------------- Phase 5: badges

async def _set_for(db, place_id, label):
    return (await db.execute(select(QuestPlaceSet).where(
        QuestPlaceSet.place_id == place_id, QuestPlaceSet.window_label == label))).scalar_one_or_none()


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
    ps = await _set_for(db, place_id, label)
    if not ps:
        return {"error": "no species-set for this place/window"}
    f, t = window_dates(label, year)
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
          AND captured_at >= :f AND captured_at < (CAST(:t AS date) + 1)
          AND ST_Contains((SELECT geom FROM quest_places WHERE id=:p),
                          ST_SetSRID(ST_MakePoint(lng, lat), 4326))
    """), {"dk": device_key, "f": f, "t": t, "p": place_id})).all()
    sset = set(ps.species_set or [])
    set_genera = {k.split(" ")[0] for k in sset if k}
    exact: set[str] = set()
    soft: set[str] = set()      # genus-level «almost» (shown as «похоже» on the client)
    for cands, top_latin, top_score in rows:
        ex, sf = _credit_keys(cands, top_latin, top_score, sset, set_genera)
        exact |= ex
        soft |= sf
    soft -= exact
    matched = sorted(exact | soft)
    m = len(matched)
    badge_id = f"{place_id}:{label}:{year}"
    tiers = tier_thresholds(ps.target, len(sset))
    issued = await _issued_tiers(db, badge_id, device_key)
    earned = [tr for tr in tiers if tr["need"] <= m]
    current_tier = max((tr["tier"] for tr in earned), default=0)
    claimable_tier = max((tr["tier"] for tr in earned if tr["tier"] not in issued), default=None)
    not_earned = [tr for tr in tiers if tr["need"] > m]
    next_need = not_earned[0]["need"] if not_earned else None
    return {"badge_id": badge_id, "set_size": len(sset), "matched": m,
            "target": ps.target, "tiers": tiers, "current_tier": current_tier,
            "claimable_tier": claimable_tier, "next_need": next_need,
            "matched_keys": matched, "soft_keys": sorted(soft)}


async def claim_badge(db: AsyncSession, device_key: str, place_id: str, label: str, year: int) -> dict:
    """Issue every tier the device has EARNED but not yet claimed, up to the highest,
    while the window is open. Each tier gets its own per-tier ordinal (scarcity).
    Idempotent per (badge_id, device, tier). The response headlines the HIGHEST tier
    granted; `granted` lists all rungs issued this call."""
    prog = await badge_progress(db, device_key, place_id, label, year)
    if "error" in prog:
        return prog
    badge_id = prog["badge_id"]
    _, t = window_dates(label, year)
    if date.today() > t:
        return {**prog, "issued": False, "reason": "window_closed"}
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
    genera = {k.split(" ")[0] for k in target_keys if k}
    found: set = set()
    for cands, tl, ts in rows:
        ex, sf = _credit_keys(cands, tl, ts, target_keys, genera)
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
    is_following = None
    if viewer_device_key:
        vk = _try_uuid(viewer_device_key)
        if vk is not None:
            n = (await db.execute(select(func.count()).select_from(QuestFollow).where(
                QuestFollow.follower_key == vk, QuestFollow.followee_handle == handle))).scalar() or 0
            is_following = n > 0
    return {
        "handle": handle,
        "nick": (dev.nickname if dev else None) or auto_nick(dk),
        "avatar": dev.avatar if dev else None,
        "level": _level_for(species),
        "score": me.get("score", 0),
        "rank": me.get("rank"),
        "badges": shelf,
        "is_following": is_following,
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
    await db.execute(pg_insert(QuestFollow).values(
        follower_key=vk, followee_handle=th).on_conflict_do_nothing(
        index_elements=["follower_key", "followee_handle"]))
    await db.commit()
    return {"status": "ok", "following": True, "handle": th}


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
    # species count per followee (one grouped query)
    out = []
    for dk, a in actors.items():
        out.append({**a})
    return {"following": out}


async def feed(db, device_key, limit: int = 30) -> dict:
    """Merged recent activity of followees: in-corpus identifications + badges.
    No coordinates; excludes blocked + activity_public=false."""
    vk = uuid_or(device_key)
    handles = (await db.execute(select(QuestFollow.followee_handle).where(
        QuestFollow.follower_key == vk))).scalars().all()
    actors = await _actors_for(db, handles, public_only=True)
    if not actors:
        return {"events": []}
    keys = list(actors.keys())

    ids = (await db.execute(select(
        Identification.device_key, Identification.matched_plant_id, Identification.created_at).where(
        Identification.device_key.in_(keys), Identification.matched_plant_id.isnot(None))
        .order_by(Identification.created_at.desc()).limit(limit))).all()
    plant_ids = list({pid for _, pid, _ in ids if pid})
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
    for dk, pid, at in ids:
        pl = plants.get(pid)
        if not pl:
            continue
        events.append({"type": "id", "actor": actors[dk],
                       "plant": {"id": pl["id"], "name": pl.get("name_modern") or pl["name"], "photo": pl["photo"]},
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
