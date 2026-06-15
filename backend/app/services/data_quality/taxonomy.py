"""GBIF external-truth resolver + cache population.

Resolves a plant's `_latin_key` (genus+species) against GBIF's `/species/match`
and caches the result in `gbif_taxon_cache`. Populated incrementally by a paced
endpoint (`POST /api/quality/resolve-taxonomy`) so the ~thousands of distinct
binomials get resolved over a few calls without a long-running request. The
identity validators then read the cache (no live calls at sweep time).
"""
import asyncio

import httpx
from sqlalchemy import select

from app.database import async_session
from app.models.plant import Plant
from app.models.gbif_cache import GbifTaxonCache
from app.services.plant_matching import _latin_key

GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"


async def _resolve_one(client: httpx.AsyncClient, latin_key: str) -> dict | None:
    try:
        r = await client.get(GBIF_MATCH_URL, params={"name": latin_key})
        d = r.json()
    except Exception:
        return None  # transient — don't cache, retry next batch
    return {
        "latin_key": latin_key,
        "match_type": d.get("matchType"),
        "confidence": d.get("confidence"),
        "kingdom": d.get("kingdom"),
        "canonical": d.get("canonicalName") or d.get("scientificName"),
        "rank": d.get("rank"),
        "status": d.get("status"),
        "usage_key": d.get("usageKey"),
    }


async def populate_cache(limit: int = 500, concurrency: int = 6) -> dict:
    """Resolve up to `limit` not-yet-cached plant latin_keys against GBIF.

    Idempotent + resumable: call repeatedly until `resolved` is 0. Returns how
    many it resolved this batch and how many distinct keys remain uncached.
    """
    async with async_session() as db:
        latins = (await db.execute(
            select(Plant.name_latin).where(Plant.name_latin.isnot(None))
        )).all()
        cached = {k for (k,) in (await db.execute(select(GbifTaxonCache.latin_key))).all()}

    all_keys = {k for (latin,) in latins if (k := _latin_key(latin))}
    todo = sorted(all_keys - cached)
    batch = todo[:limit]
    if not batch:
        return {"resolved": 0, "remaining": 0, "total_distinct": len(all_keys),
                "cached_total": len(cached)}

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(client, k):
        async with sem:
            return await _resolve_one(client, k)

    async with httpx.AsyncClient(timeout=20) as client:
        results = await asyncio.gather(*[_guarded(client, k) for k in batch])

    rows = [r for r in results if r]
    async with async_session() as db:
        for r in rows:
            db.add(GbifTaxonCache(**r))
        await db.commit()

    return {"resolved": len(rows), "remaining": max(0, len(todo) - len(rows)),
            "total_distinct": len(all_keys), "cached_total": len(cached) + len(rows)}
