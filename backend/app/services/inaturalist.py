"""iNaturalist enrichment: resolve each plant's Latin name to an iNat taxon and
pull a canonical, license-clean photo.

Design notes (validated against the live API):
- The bridge is ALWAYS ``name_latin`` → ``taxon_id`` via ``GET /v1/taxa?q=``.
  Searching observations by name directly is fuzzy and returns wrong species.
- iNat asks for ≤60 req/min; the corpus pass paces itself with a sleep.
- The product may be commercial, so we only persist a photo whose ``license_code``
  permits that (CC0 / CC-BY / CC-BY-SA). Other photos (CC *-NC, *-ND, or
  All-Rights-Reserved/None) are skipped — but we still record ``inat_taxon_id``
  so the future "find nearby observations" feature works regardless.
- Attribution is always stored and must always be displayed.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import Plant

logger = logging.getLogger(__name__)

INAT_BASE = "https://api.inaturalist.org/v1"
# Be a polite API citizen — iNat asks identifiable clients to set a UA.
_HEADERS = {"User-Agent": "historical-recipes/1.0 (herbarium enrichment; contact via hist.begemot26.ru)"}

# Licenses we may reuse in a possibly-commercial product (always with attribution).
COMMERCIAL_OK_LICENSES = {"cc0", "cc-by", "cc-by-sa"}

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
    photo_license, common_name} or None if no usable match. Photo fields are
    None when the species has no photo or its license isn't reusable.

    On HTTP 429 (iNat throttle) backs off and retries a few times, honoring a
    ``Retry-After`` header when present; if still throttled, returns None so the
    plant stays unsynced and gets picked up on a later pass."""
    query = _clean_binomial(name_latin)
    if not query:
        return None
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
        return None

    out = {
        "taxon_id": taxon.get("id"),
        "common_name": taxon.get("preferred_common_name"),
        "photo_url": None,
        "photo_attribution": None,
        "photo_license": None,
    }
    photo = taxon.get("default_photo") or {}
    license_code = (photo.get("license_code") or "").lower()
    if photo and license_code in COMMERCIAL_OK_LICENSES:
        # medium_url (~500px) is the display size; the frontend swaps "medium"
        # → "square" for the list thumbnail.
        out["photo_url"] = photo.get("medium_url") or photo.get("url")
        out["photo_attribution"] = photo.get("attribution")
        out["photo_license"] = license_code
    elif photo and license_code:
        logger.info(f"iNat photo for {query!r} skipped: license {license_code!r} not reusable")
    return out


async def enrich_plants_inat(
    db: AsyncSession,
    dry_run: bool = True,
    limit: int | None = None,
    force: bool = False,
    pace_seconds: float = 1.6,
) -> dict:
    """Corpus-wide enrichment pass. Resolves each plant's ``name_latin`` to an
    iNat taxon and stores a license-clean photo. Idempotent & resumable: by
    default only touches plants not yet synced (``inat_synced_at`` NULL); pass
    ``force=True`` to refresh everything. ``limit`` bounds one call so it stays
    under proxy timeouts — run repeatedly until ``remaining`` hits 0.
    """
    stmt = select(Plant).where(Plant.name_latin.isnot(None))
    if not force:
        stmt = stmt.where(Plant.inat_synced_at.is_(None))
    stmt = stmt.order_by(Plant.name)
    if limit:
        stmt = stmt.limit(limit)
    plants = (await db.execute(stmt)).scalars().all()

    processed = taxa_resolved = photos_set = no_match = 0
    plan: list[dict] = []
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=30) as client:
        for i, p in enumerate(plants):
            processed += 1
            iconic = _ICONIC_FOR_KINGDOM.get(p.kingdom or "растение", "Plantae")
            res = await resolve_taxon_photo(client, p.name_latin, iconic=iconic)
            if res is None or not res.get("taxon_id"):
                no_match += 1
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
            # Pace to respect ≤60 req/min (skip the trailing sleep).
            if i < len(plants) - 1:
                await asyncio.sleep(pace_seconds)

    if not dry_run:
        await db.commit()

    remaining = (await db.execute(
        select(func.count()).select_from(Plant)
        .where(Plant.name_latin.isnot(None), Plant.inat_synced_at.is_(None))
    )).scalar()

    summary = {
        "dry_run": dry_run,
        "processed": processed,
        "taxa_resolved": taxa_resolved,
        "photos_set": photos_set,
        "no_match": no_match,
        "remaining": remaining,
        "plan": plan,
    }
    logger.info(f"iNat enrich: {summary}")
    return summary
