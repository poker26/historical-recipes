"""iNaturalist enrichment: resolve each plant's Latin name to an iNat taxon and
pull a canonical, license-clean photo.

Design notes (validated against the live API):
- The bridge is ALWAYS ``name_latin`` → ``taxon_id`` via ``GET /v1/taxa?q=``.
  Searching observations by name directly is fuzzy and returns wrong species.
- iNat asks for ≤60 req/min; the corpus pass paces itself with a sleep.
- Photos are shown only on the internal (mTLS-locked) herbarium admin UI as a
  curation aid, not as resold data, so we persist any Creative-Commons-licensed
  default photo (``DISPLAY_OK_LICENSES``, incl. the *-NC / *-ND variants — CC-BY-NC
  is the commonest license on iNat). Only All-Rights-Reserved / unlicensed photos
  are skipped. The stored ``photo_license`` lets a future paid/public surface
  re-tier down to the commercial-safe subset without re-fetching.
- Attribution is always stored and must always be displayed.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import Plant, PlantBookMention

logger = logging.getLogger(__name__)

INAT_BASE = "https://api.inaturalist.org/v1"
# Be a polite API citizen — iNat asks identifiable clients to set a UA.
_HEADERS = {"User-Agent": "historical-recipes/1.0 (herbarium enrichment; contact via hist.begemot26.ru)"}

# Licenses we may reuse in a possibly-commercial product (always with attribution).
# Kept for future tiering: when photos ever surface in the paid/public product we
# filter the served set down to this subset.
COMMERCIAL_OK_LICENSES = {"cc0", "cc-by", "cc-by-sa"}

# Licenses we accept for DISPLAY on the internal (mTLS-locked) herbarium admin UI.
# Photos here are a curation aid, not resold data, so any Creative Commons license
# is fine as long as attribution is shown (which we always store). This deliberately
# includes the *-NC / *-ND variants (CC-BY-NC is by far the commonest license on
# iNat). All-Rights-Reserved / unlicensed (null code) is still skipped. The stored
# `photo_license` lets us re-tier to COMMERCIAL_OK_LICENSES later without re-fetching.
DISPLAY_OK_LICENSES = {
    "cc0", "cc-by", "cc-by-sa", "cc-by-nd",
    "cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd",
}

# Constrain matches to the right kingdom so an epithet collision (e.g. a plant
# and an animal both named "* japonica") can never pull in the wrong creature.
_ICONIC_FOR_KINGDOM = {"растение": "Plantae", "гриб": "Fungi"}

# Drop the author citation from a binomial so the iNat query is clean:
# "Gratiola officinalis L." → "Gratiola officinalis", "Mentha × piperita" → "Mentha piperita".
_NON_ALPHA = re.compile(r"[^a-zA-Zа-яёА-ЯЁ\s]")


def _clean_binomial(name_latin: str | None) -> str | None:
    if not name_latin:
        return None
    s = name_latin.replace("×", " ")
    s = _NON_ALPHA.sub(" ", s)
    toks = [t for t in s.split() if t.lower() != "x"]
    if len(toks) < 2:
        # genus-only or junk — still usable as a genus query, but skip: a
        # genus photo would mislabel the species page.
        return None
    genus, species = toks[0], toks[1]
    # An abbreviated genus ("A. japonica", "G. robertianum" — a determiner shorthand
    # after first mention) is unrecoverable: the single letter makes iNat fuzzy-match
    # the epithet to the wrong species, even the wrong kingdom. Refuse it.
    if len(genus) <= 1:
        return None
    return f"{genus} {species}"


def _pick_taxon(results: list[dict], wanted: str, iconic: str | None = None) -> dict | None:
    """Choose the best taxon match within the requested kingdom. Prefer an exact
    (case-insensitive) species name; otherwise the first active species; otherwise
    the first candidate. If ``iconic`` is set and no result is in that kingdom,
    return None rather than mismatch across kingdoms."""
    cands = results
    if iconic:
        cands = [r for r in results if (r.get("iconic_taxon_name") or "") == iconic]
        if not cands:
            return None
    wanted_l = wanted.lower()
    species = [r for r in cands if r.get("rank") == "species"]
    for r in species:
        if (r.get("name") or "").lower() == wanted_l:
            return r
    if species:
        return species[0]
    return cands[0] if cands else None


async def resolve_taxon_photo(client: httpx.AsyncClient, name_latin: str, iconic: str | None = None) -> dict | None:
    """Resolve one Latin name to {taxon_id, photo_url, photo_attribution,
    photo_license, common_name}. Photo fields are None when the species has no
    photo or its license isn't reusable.

    Return contract distinguishes two "no photo" cases so the corpus sweep can
    terminate instead of re-querying the same plants forever:

    * ``None``                → TRANSIENT failure (HTTP error, 429 throttle,
      bad/empty response). Caller leaves the plant UNSYNCED to retry later.
    * ``{"taxon_id": None}``  → DEFINITIVE no usable match (unrecoverable Latin
      name, or iNat has no such taxon). Caller marks it SYNCED so we stop
      hitting iNat for it.

    On HTTP 429 backs off and retries a few times, honoring ``Retry-After``."""
    query = _clean_binomial(name_latin)
    if not query:
        return {"taxon_id": None}  # unusable Latin (abbrev/genus-only) — never resolvable
    params = {"q": query, "rank": "species", "is_active": "true", "per_page": 5, "locale": "ru"}
    if iconic:
        params["iconic_taxa"] = iconic
    results = None
    for attempt in range(4):
        try:
            resp = await client.get(f"{INAT_BASE}/taxa", params=params, headers=_HEADERS)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"iNat taxa error for {query!r}: {type(e).__name__}: {e}")
            return None
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if (retry_after or "").isdigit() else (5 * (attempt + 1))
            logger.warning(f"iNat 429 for {query!r}; backing off {delay}s (attempt {attempt+1}/4)")
            await asyncio.sleep(delay)
            continue
        if resp.status_code != 200:
            logger.warning(f"iNat taxa HTTP {resp.status_code} for {query!r}")
            return None
        try:
            results = resp.json().get("results", [])
        except ValueError as e:
            logger.warning(f"iNat taxa unparseable body for {query!r}: {e}")
            return None
        break
    if results is None:  # exhausted retries while throttled
        return None

    taxon = _pick_taxon(results, query, iconic=iconic)
    if not taxon:
        return {"taxon_id": None}  # iNat has no matching taxon in the right kingdom

    out = {
        "taxon_id": taxon.get("id"),
        "common_name": taxon.get("preferred_common_name"),
        "photo_url": None,
        "photo_attribution": None,
        "photo_license": None,
    }
    photo = taxon.get("default_photo") or {}
    license_code = (photo.get("license_code") or "").lower()
    if photo and license_code in DISPLAY_OK_LICENSES:
        # medium_url (~500px) is the display size; the frontend swaps "medium"
        # → "square" for the list thumbnail.
        out["photo_url"] = photo.get("medium_url") or photo.get("url")
        out["photo_attribution"] = photo.get("attribution")
        out["photo_license"] = license_code
    elif photo and license_code:
        logger.info(f"iNat photo for {query!r} skipped: license {license_code!r} not reusable")
    return out


# iNat-RU atlas "grid cell" community places, e.g. "Квадрат В-01 (Владимирская
# область)". Pure tiling artifacts that flood autocomplete and hijack a bare
# oblast query — never a meaningful answer to "where can I find this plant".
_GRID_CELL_RE = re.compile(r"^Квадрат\s", re.IGNORECASE)


def _place_relevant(query: str, name: str) -> bool:
    """True if an autocomplete candidate's name plausibly *is* the queried place,
    not just a place that happens to sit inside it. iNat echoes the parent region
    in parentheses (e.g. "Радованье (Владимирская область)"), so a bare-oblast
    query like "Владимирская область" matches a random village's parenthetical and
    would otherwise be picked. We require a real overlap between a query word and
    the candidate's *own* name (the part before any " (" or ","): some 4+-char
    query token must share a 4-char prefix with some name token — enough to absorb
    Russian case endings ("Суздаль" ↔ "Суздальский", "Собинский" ↔ "Собинском")
    while rejecting "Владимирская"/"область" ↔ "Радованье"."""
    core = re.split(r"\s*[(,]", name, maxsplit=1)[0]
    name_toks = [t for t in re.findall(r"\w+", core.lower()) if len(t) >= 4]
    query_toks = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 4]
    if not name_toks or not query_toks:
        return False
    return any(qt[:4] == nt[:4] for qt in query_toks for nt in name_toks)


async def resolve_place(client: httpx.AsyncClient, query: str) -> dict | None:
    """Resolve a free-text place name (a district, town vicinity, region — any
    granularity) to an iNat ``place_id`` via ``GET /v1/places/autocomplete``.

    Picking heuristic: autocomplete's first hit is unreliable for "where to look"
    queries — e.g. "Суздаль" ranks "Суздальский парк" (a point) above "Суздальский
    район". We instead take the candidate with the LARGEST ``bbox_area``, which is
    the district/region-sized place a "where can I find X around here" question
    actually means.

    Caveat handled here: an iNat-RU atlas project tiles every oblast into uniform
    "Квадрат X-NN (область)" grid cells. These are community places (no
    ``admin_level``, no ``place_type`` — indistinguishable structurally from a
    real район) and they FLOOD autocomplete for a bare oblast name, hijacking the
    largest-bbox pick with a meaningless cell. We drop them by name so a district
    query stays correct and an oblast query degrades to "not found" (the real
    oblast boundary isn't surfaced for the Russian name anyway) rather than
    silently returning a wrong grid cell. Returns ``None`` on no match or
    transport error so the caller degrades gracefully. ``display_name`` is echoed
    back so the consumer can tell the user which place was used (covers the
    deliberate imprecision)."""
    try:
        resp = await client.get(f"{INAT_BASE}/places/autocomplete",
                                params={"q": query}, headers=_HEADERS)
        if resp.status_code != 200:
            logger.warning(f"iNat places HTTP {resp.status_code} for {query!r}")
            return None
        results = resp.json().get("results", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"iNat places error for {query!r}: {type(e).__name__}: {e}")
        return None
    results = [p for p in results
               if not _GRID_CELL_RE.match(str(p.get("name") or ""))
               and _place_relevant(query, str(p.get("name") or ""))]
    if not results:
        return None
    best = max(results, key=lambda p: p.get("bbox_area") or 0.0)
    return {
        "place_id": best.get("id"),
        "display_name": best.get("display_name"),
        "bbox_area": best.get("bbox_area"),
    }


async def _obs_total(client: httpx.AsyncClient, scope: dict) -> int | None:
    """Cheap frequency: total research-grade observations matching ``scope``
    (per_page=0 returns only the count, no records). None on error."""
    try:
        resp = await client.get(f"{INAT_BASE}/observations",
                                params={**scope, "per_page": 0}, headers=_HEADERS)
        if resp.status_code != 200:
            return None
        return resp.json().get("total_results")
    except (httpx.HTTPError, ValueError):
        return None


async def _obs_seasonality(client: httpx.AsyncClient, scope: dict) -> dict | None:
    """Phenology: observation counts binned by calendar month (1–12) — answers
    "when in the year to look". None on error."""
    try:
        resp = await client.get(f"{INAT_BASE}/observations/histogram", headers=_HEADERS,
                                params={**scope, "date_field": "observed",
                                        "interval": "month_of_year"})
        if resp.status_code != 200:
            return None
        hist = resp.json().get("results", {}).get("month_of_year", {})
        return {int(k): v for k, v in hist.items()}
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def find_observations(
    taxon_id: int,
    *,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 50.0,
    place: str | None = None,
    place_id: int | None = None,
    limit: int = 20,
) -> dict:
    """Live "where can I find this" lookup over research-grade iNat sightings of one
    taxon. The plant's ``inat_taxon_id`` is resolved upstream (caller passes it in).

    Scope is EITHER a named place (``place`` free-text → resolved to ``place_id``,
    or an explicit ``place_id``) OR a coordinate circle (``lat``/``lng`` + ``radius_km``).
    A named place is the natural fit for questions like "где в Собинском районе /
    окрестностях Суздаля поискать барвинок".

    Returns: ``total_count`` (how common it is in that scope), ``seasonality`` (per-
    month observation counts — when to look), and a newest-first sample of
    ``observations`` each carrying ``place_guess`` (where exactly) + coords + photo
    and attribution (iNat ToS requires displaying it). Never raises — on any error
    it returns empty fields plus an ``error``/``note`` so a consumer degrades."""
    async with httpx.AsyncClient(timeout=30) as client:
        place_info = None
        if place_id is None and place:
            place_info = await resolve_place(client, place)
            if place_info is None:
                return {"taxon_id": taxon_id, "scope": None, "total_count": None,
                        "seasonality": None, "count": 0, "observations": [],
                        "error": f"place not found: {place!r}"}
            place_id = place_info["place_id"]

        if place_id is not None:
            scope = {"place_id": place_id}
            scope_label = place_info["display_name"] if place_info else f"place {place_id}"
        elif lat is not None and lng is not None:
            scope = {"lat": lat, "lng": lng, "radius": radius_km}
            scope_label = f"{radius_km} km around {lat},{lng}"
        else:
            return {"taxon_id": taxon_id, "scope": None, "total_count": None,
                    "seasonality": None, "count": 0, "observations": [],
                    "error": "provide either a place/place_id or lat+lng"}

        base = {"taxon_id": taxon_id, "quality_grade": "research", "locale": "ru", **scope}

        total = await _obs_total(client, base)
        seasonality = await _obs_seasonality(client, base)

        list_params = {**base, "per_page": min(max(limit, 1), 50),
                       "order_by": "observed_on", "order": "desc",
                       "photos": "true", "geo": "true"}
        try:
            resp = await client.get(f"{INAT_BASE}/observations", params=list_params, headers=_HEADERS)
        except httpx.HTTPError as e:
            logger.warning(f"iNat observations error for taxon {taxon_id}: {type(e).__name__}: {e}")
            return {"taxon_id": taxon_id, "scope": scope_label, "place_id": place_id,
                    "total_count": total, "seasonality": seasonality,
                    "count": 0, "observations": [], "error": str(e)}
        if resp.status_code != 200:
            return {"taxon_id": taxon_id, "scope": scope_label, "place_id": place_id,
                    "total_count": total, "seasonality": seasonality,
                    "count": 0, "observations": [], "error": f"iNat HTTP {resp.status_code}"}
        try:
            results = resp.json().get("results", [])
        except ValueError as e:
            return {"taxon_id": taxon_id, "scope": scope_label, "place_id": place_id,
                    "total_count": total, "seasonality": seasonality,
                    "count": 0, "observations": [], "error": f"bad body: {e}"}

    obs: list[dict] = []
    for r in results:
        photos = r.get("photos") or []
        photo = photos[0] if photos else {}
        user = r.get("user") or {}
        obs.append({
            "id": r.get("id"),
            "observed_on": r.get("observed_on"),
            "place_guess": r.get("place_guess"),
            "location": r.get("location"),  # "lat,lng" string
            "uri": r.get("uri"),
            "observer": user.get("login"),
            "quality_grade": r.get("quality_grade"),
            "photo_url": photo.get("url"),
            "photo_attribution": photo.get("attribution"),
            "photo_license": photo.get("license_code"),
        })
    return {"taxon_id": taxon_id, "scope": scope_label, "place_id": place_id,
            "total_count": total, "seasonality": seasonality,
            "count": len(obs), "observations": obs}


async def enrich_plants_inat(
    db: AsyncSession,
    dry_run: bool = True,
    limit: int | None = None,
    force: bool = False,
    pace_seconds: float = 1.6,
    book_id=None,
    progress=None,
) -> dict:
    """Enrichment pass. Resolves each plant's ``name_latin`` to an iNat taxon and
    stores a license-clean photo. Idempotent & resumable: by default only touches
    plants not yet synced (``inat_synced_at`` NULL); pass ``force=True`` to refresh
    everything. ``limit`` bounds one call so it stays under proxy timeouts.

    ``book_id`` scopes the pass to plants mentioned in one book — the per-book
    pipeline step uses this so a freshly-ingested book enriches only its own new
    plants. ``progress(done, total, name)`` is an optional callback (the Temporal
    activity wires it to ``activity.heartbeat``).

    Termination: a DEFINITIVE no-match marks the plant synced (so the corpus
    sweep eventually drains ``remaining`` to a floor of only transiently-throttled
    plants), while a transient throttle leaves it unsynced to retry on a later
    pass — counted separately as ``throttled``.
    """
    stmt = select(Plant).where(Plant.name_latin.isnot(None))
    if not force:
        stmt = stmt.where(Plant.inat_synced_at.is_(None))
    if book_id is not None:
        stmt = stmt.where(Plant.id.in_(
            select(PlantBookMention.plant_id).where(PlantBookMention.book_id == book_id)
        ))
    stmt = stmt.order_by(Plant.name)
    if limit:
        stmt = stmt.limit(limit)
    plants = (await db.execute(stmt)).scalars().all()

    processed = taxa_resolved = photos_set = no_match = throttled = 0
    plan: list[dict] = []
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=30) as client:
        for i, p in enumerate(plants):
            processed += 1
            iconic = _ICONIC_FOR_KINGDOM.get(p.kingdom or "растение", "Plantae")
            res = await resolve_taxon_photo(client, p.name_latin, iconic=iconic)
            if res is None:
                # Transient (throttle/HTTP): leave unsynced so a later pass retries.
                throttled += 1
            elif not res.get("taxon_id"):
                # Definitive no match: mark synced so we stop re-querying iNat for it.
                no_match += 1
                if not dry_run:
                    p.inat_synced_at = now
            else:
                taxa_resolved += 1
                if res.get("photo_url"):
                    photos_set += 1
                if not dry_run:
                    p.inat_taxon_id = res["taxon_id"]
                    p.photo_url = res.get("photo_url")
                    p.photo_attribution = res.get("photo_attribution")
                    p.photo_license = res.get("photo_license")
                    p.photo_source = "inaturalist" if res.get("photo_url") else None
                    p.inat_synced_at = now
                if len(plan) < 25:
                    plan.append({
                        "plant": p.name,
                        "name_latin": p.name_latin,
                        "taxon_id": res["taxon_id"],
                        "has_photo": bool(res.get("photo_url")),
                        "license": res.get("photo_license"),
                    })
            if progress is not None:
                try:
                    progress(i + 1, len(plants), p.name)
                except Exception:
                    pass
            # Pace to respect ≤60 req/min (skip the trailing sleep).
            if i < len(plants) - 1:
                await asyncio.sleep(pace_seconds)

    if not dry_run:
        await db.commit()

    remaining_stmt = (
        select(func.count()).select_from(Plant)
        .where(Plant.name_latin.isnot(None), Plant.inat_synced_at.is_(None))
    )
    if book_id is not None:
        remaining_stmt = remaining_stmt.where(Plant.id.in_(
            select(PlantBookMention.plant_id).where(PlantBookMention.book_id == book_id)
        ))
    remaining = (await db.execute(remaining_stmt)).scalar()

    summary = {
        "dry_run": dry_run,
        "processed": processed,
        "taxa_resolved": taxa_resolved,
        "photos_set": photos_set,
        "no_match": no_match,
        "throttled": throttled,
        "remaining": remaining,
        "plan": plan,
    }
    logger.info(f"iNat enrich: {summary}")
    return summary
