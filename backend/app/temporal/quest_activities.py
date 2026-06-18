# -*- coding: utf-8 -*-
"""Durable activities that build quests data at scale (Phase 6):
  - osm_ingest_region_activity: tile a region bbox and ingest named OSM places
    (Overpass) into quest_places — paced, idempotent, heartbeating.
  - build_place_sets_activity: precompute the species-set (badge target) for every
    place that lacks one for a given half-month window (iNat species_counts) —
    paced, idempotent, heartbeating.
Both resume after a worker restart (idempotent: ON CONFLICT / skip-existing).
"""
import logging

import asyncio

import httpx
from sqlalchemy import text
from temporalio import activity

from app.database import async_session
from app.services import osm
from app.services import quests as quests_svc

logger = logging.getLogger(__name__)


@activity.defn
async def osm_ingest_region_activity(s: float, w: float, n: float, e: float, tile: float = 0.1) -> dict:
    """Tile [s,w]→[n,e] into `tile`-degree cells and ingest named OSM places per
    cell (smaller cells keep each Overpass query light). Idempotent (osm_id upsert)."""
    tiles = inserted = updated = errors = 0
    lat = s
    while lat < n:
        lng = w
        while lng < e:
            try:
                async with async_session() as db:
                    r = await osm.ingest_bbox(db, lat, lng, min(lat + tile, n), min(lng + tile, e))
                inserted += r.get("inserted", 0)
                updated += r.get("updated", 0)
            except Exception as ex:
                errors += 1
                logger.warning("osm tile %s,%s failed: %s", lat, lng, str(ex)[:80])
            tiles += 1
            activity.heartbeat({"tiles": tiles, "inserted": inserted, "updated": updated, "errors": errors})
            lng = round(lng + tile, 6)
        lat = round(lat + tile, 6)
    return {"tiles": tiles, "inserted": inserted, "updated": updated, "errors": errors}


@activity.defn
async def build_place_sets_activity(window_label: str) -> dict:
    """Compute the species-set for every quest_place missing one for this window.
    Idempotent: only touches places without a set for `window_label`."""
    async with async_session() as db:
        ids = [str(r[0]) for r in (await db.execute(text(
            "SELECT p.id FROM quest_places p WHERE NOT EXISTS "
            "(SELECT 1 FROM quest_place_sets s WHERE s.place_id=p.id AND s.window_label=:w)"),
            {"w": window_label})).all()]
    built = low_density = 0
    for i, pid in enumerate(ids):
        try:
            async with async_session() as db:
                res = await quests_svc.compute_species_set(db, pid, window_label)
            if res.get("set_size"):
                built += 1
            else:
                low_density += 1
        except Exception as ex:
            logger.warning("set build %s failed: %s", pid, str(ex)[:80])
        activity.heartbeat({"done": i + 1, "total": len(ids), "built": built, "low_density": low_density})
    return {"total": len(ids), "built": built, "low_density": low_density}


@activity.defn
async def place_biotope_activity() -> dict:
    """GPS→biotope precompute (biotope domain half b): for every quest_place lacking
    biotopes, ask Overpass what landcover its bbox holds and map to the 18 canonical
    biotopes (quest_place_biotopes). NULL-marker = processed, no landcover → resumable
    + guaranteed termination. Overpass-paced (Semaphore 2, fair-use). No iNat."""
    from app.services.place_biotope import build_place_biotopes
    done = tagged = empty = 0
    sem = asyncio.Semaphore(2)

    async with httpx.AsyncClient(timeout=120) as client:
        async def _one(pid):
            async with sem:
                async with async_session() as db:
                    try:
                        bios = await asyncio.wait_for(build_place_biotopes(db, pid, client), timeout=120)
                    except Exception:
                        bios = set()
                return pid, bios

        while True:
            async with async_session() as db:
                rows = (await db.execute(text(
                    "SELECT id::text FROM quest_places WHERE id NOT IN "
                    "(SELECT place_id FROM quest_place_biotopes) LIMIT 40"))).all()
            if not rows:
                break
            for pid, bios in await asyncio.gather(*[_one(r[0]) for r in rows]):
                async with async_session() as db:
                    if bios:
                        for b in bios:
                            await db.execute(text(
                                "INSERT INTO quest_place_biotopes (place_id, biotope) VALUES (:p, :b)"),
                                {"p": pid, "b": b})
                        tagged += 1
                    else:
                        await db.execute(text(
                            "INSERT INTO quest_place_biotopes (place_id, biotope) VALUES (:p, NULL)"),
                            {"p": pid})
                        empty += 1
                    await db.commit()
                done += 1
                activity.heartbeat({"done": done, "tagged": tagged, "empty": empty})
    return {"phase": "place_biotope", "done": done, "tagged": tagged, "empty": empty}
