"""OpenStreetMap named-place ingest + point→place lookup (quests Phase 2).

Pulls named parks / forests / reserves (ways + multipolygon relations) from the
Overpass API inside a bounding box, converts the Overpass JSON to GeoJSON
(osm2geojson handles relation ring assembly), and stores each polygon in the
PostGIS `quest_places` table. `point_to_place` then answers "which named place
covers this GPS point" via ST_Contains, smallest-area (most specific) first.
"""
import json
import logging

import httpx
import osm2geojson
from sqlalchemy import text

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass returns 406 Not Acceptable without an identifiable User-Agent.
_HEADERS = {"User-Agent": "chto-rastet-quests/1.0 (botanical walks app)"}


def _kind(tags: dict) -> str:
    if tags.get("leisure") in ("nature_reserve",) or tags.get("boundary") == "protected_area":
        return "reserve"
    if tags.get("landuse") == "forest" or tags.get("natural") == "wood":
        return "forest"
    if tags.get("leisure") in ("park", "garden"):
        return "park"
    return "zone"


def _overpass_query(s: float, w: float, n: float, e: float) -> str:
    bbox = f"{s},{w},{n},{e}"
    return f"""
[out:json][timeout:120];
(
  way["leisure"~"^(park|nature_reserve|garden)$"]["name"]({bbox});
  relation["leisure"~"^(park|nature_reserve|garden)$"]["name"]({bbox});
  way["landuse"="forest"]["name"]({bbox});
  relation["landuse"="forest"]["name"]({bbox});
  way["natural"="wood"]["name"]({bbox});
  relation["boundary"="protected_area"]["name"]({bbox});
);
out geom;
"""


async def ingest_bbox(db, s: float, w: float, n: float, e: float) -> dict:
    """Fetch + upsert named places inside a bbox. Idempotent (ON CONFLICT osm_id)."""
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(OVERPASS_URL, data={"data": _overpass_query(s, w, n, e)}, headers=_HEADERS)
        r.raise_for_status()
        overpass = r.json()

    fc = osm2geojson.json2geojson(overpass)  # FeatureCollection, relations assembled
    inserted = updated = skipped = 0
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            skipped += 1
            continue
        props = feat.get("properties") or {}
        tags = props.get("tags") or {}
        name = tags.get("name")
        if not name:
            skipped += 1
            continue
        osm_id = f"{props.get('type', 'way')}/{props.get('id')}"
        try:
            res = await db.execute(text("""
                INSERT INTO quest_places (osm_id, name, kind, geom, area)
                VALUES (
                    :osm_id, :name, :kind,
                    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:gj), 4326)), 3)),
                    ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:gj), 4326)::geography)
                )
                ON CONFLICT (osm_id) DO UPDATE SET
                    name=EXCLUDED.name, kind=EXCLUDED.kind, geom=EXCLUDED.geom,
                    area=EXCLUDED.area, updated_at=now()
                RETURNING (xmax = 0) AS inserted
            """), {"osm_id": osm_id, "name": name, "kind": _kind(tags),
                   "gj": json.dumps(geom)})
            row = res.first()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
        except Exception as ex:
            logger.warning("place insert failed for %s (%s): %s", osm_id, name, str(ex)[:80])
            skipped += 1
    await db.commit()
    return {"features": len(fc.get("features", [])), "inserted": inserted,
            "updated": updated, "skipped": skipped}


async def point_to_place(db, lat: float, lng: float) -> list[dict]:
    """Named places covering (lat,lng), most specific (smallest area) first."""
    rows = (await db.execute(text("""
        SELECT id::text, osm_id, name, kind, area
        FROM quest_places
        WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326))
        ORDER BY area ASC NULLS LAST
    """), {"lat": lat, "lng": lng})).all()
    return [{"id": r[0], "osm_id": r[1], "name": r[2], "kind": r[3], "area": r[4]} for r in rows]
