"""GPS→biotope precompute (biotope domain, half b — quests Phase 6).

For each named ``quest_place`` (a park/forest/reserve polygon) we ask Overpass what
LANDCOVER it contains (forest + leaf type, meadow, water, wetland, scree…) and map
the OSM tags onto the SAME 18 canonical biotopes the plant side uses (see
``app/services/biotope.py``). The result — ``quest_place_biotopes`` — lets a walk
answer «найди здесь виды биотопа X»: join place→biotope with plant→biotope.

bbox-level (not polygon-clipped) for v1: a park's bbox landcover ≈ the park's. The
place's own ``kind`` (forest/park) seeds a coarse biotope too. Overpass-only, no iNat.
"""
import json
import logging

import httpx
import osm2geojson
from sqlalchemy import text

from app.services.biotope import BIOTOPES

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_HEADERS = {"User-Agent": "chto-rastet-quests/1.0 (botanical walks app)"}

_BSET = set(BIOTOPES)


def _tags_to_biotope(tags: dict) -> str | None:
    """Map an OSM landcover element's tags to ONE canonical biotope (or None).
    Strings MUST match BIOTOPES exactly."""
    nat = tags.get("natural")
    lu = tags.get("landuse")
    lei = tags.get("leisure")
    leaf = tags.get("leaf_type")
    if nat == "wood" or lu == "forest":
        return {"broadleaved": "лес лиственный", "needleleaved": "лес хвойный",
                "mixed": "лес смешанный"}.get(leaf, "лес")
    if nat in ("scrub", "heath"):
        return "кустарники/заросли"
    if nat == "grassland" or lu in ("meadow", "grass"):
        return "луг"
    if lu in ("farmland", "vineyard", "allotments", "orchard"):
        return "сады/парки" if lu == "orchard" else "поле/сорное"
    if nat == "wetland":
        return "болото/сырое"
    if nat == "water" or lu == "reservoir":
        return "водное/прибрежное"
    if nat in ("beach", "sand", "dune"):
        return "пески/дюны/обнажения"
    if nat in ("scree", "bare_rock", "cliff", "rock"):
        return "каменистые/скалистые склоны"
    if nat in ("fell", "ridge", "peak"):
        return "горы/предгорья"
    if nat == "saltmarsh":
        return "солончаки/засоленное"
    if lei in ("park", "garden"):
        return "сады/парки"
    return None


def _overpass_landcover_query(s: float, w: float, n: float, e: float) -> str:
    bbox = f"{s},{w},{n},{e}"
    # `out geom;` — geometry needed so we can CLIP to the place polygon (the bbox of
    # a big irregular park spans half the city → bbox-only returns every biotope).
    return f"""
[out:json][timeout:120];
(
  way["natural"~"^(wood|scrub|heath|grassland|wetland|water|beach|sand|dune|scree|bare_rock|cliff|rock|fell|ridge|saltmarsh)$"]({bbox});
  way["landuse"~"^(forest|meadow|grass|farmland|orchard|vineyard|allotments|reservoir)$"]({bbox});
  way["leisure"~"^(park|garden)$"]({bbox});
  relation["natural"~"^(wood|wetland|water|scrub|grassland)$"]({bbox});
  relation["landuse"~"^(forest|meadow|farmland)$"]({bbox});
);
out geom;
"""


async def build_place_biotopes(db, place_id: str, client: httpx.AsyncClient) -> set[str]:
    """Canonical biotopes whose landcover actually INTERSECTS the place polygon
    (+ the place's own kind). Empty set → NULL marker. Clipping to the polygon (not
    just the bbox) is essential: a park's bbox spans neighbours/city otherwise."""
    row = (await db.execute(text(
        "SELECT ST_YMin(geom), ST_XMin(geom), ST_YMax(geom), ST_XMax(geom), kind "
        "FROM quest_places WHERE id=:p"), {"p": place_id})).first()
    if not row or row[0] is None:
        return set()
    s, w, n, e, kind = row

    pairs: list[tuple[str, str]] = []   # (geojson, biotope) of mappable landcover
    try:
        r = await client.post(OVERPASS_URL,
                              data={"data": _overpass_landcover_query(s, w, n, e)},
                              headers=_HEADERS)
        r.raise_for_status()
        fc = osm2geojson.json2geojson(r.json())
        for feat in fc.get("features", []):
            geom = feat.get("geometry") or {}
            if geom.get("type") not in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
                continue
            b = _tags_to_biotope((feat.get("properties") or {}).get("tags") or {})
            if b in _BSET:
                pairs.append((json.dumps(geom), b))
    except Exception as ex:
        logger.warning("overpass landcover failed for place %s: %s", place_id, str(ex)[:80])

    biotopes: set[str] = set()
    if pairs:
        # one query: which mapped landcover polygons actually touch the place geom.
        rows = (await db.execute(text("""
            SELECT DISTINCT f.biotope
            FROM unnest(cast(:gjs as text[]), cast(:bios as text[])) AS f(gj, biotope)
            WHERE ST_Intersects(
                (SELECT geom FROM quest_places WHERE id=:p),
                ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(f.gj), 4326)))
        """), {"gjs": [p[0] for p in pairs], "bios": [p[1] for p in pairs],
               "p": place_id})).all()
        biotopes = {x[0] for x in rows}

    seed = {"forest": "лес", "park": "сады/парки"}.get(kind or "")
    if seed:
        biotopes.add(seed)
    return biotopes
