"""ESA WorldCover 10m landcover → biotope at an ARBITRARY GPS point (RFC-cold-start-
nearby P1). Local GeoTIFF tiles → NO runtime egress, NO Overpass rate-limit, works
anywhere a tile is present. Coarser than OSM (10 classes, no forest leaf-type) but
enough for the «I'm in a field, not a forest» fix that the live «5 plants near you»
needs. OSM-Overpass stays for the paced batch place-enrichment; this is for live points.

Tiles: ESA WorldCover v200 (2021), 3°×3°, named by SW corner «N54E036». Download from
the public S3 bucket into WORLDCOVER_DIR. Missing tile → empty set → caller falls back
to ungated frequency (variant «а»). N/E naming only for now (Russia pilot)."""
import math
import os
import threading

import rasterio

_DIR = os.environ.get("WORLDCOVER_DIR", "/data/worldcover")

# WorldCover v200 class code → our canonical biotope(s) (subset of biotope.BIOTOPES).
_CLASS_BIOTOPE: dict[int, list[str]] = {
    10: ["лес"],                  # tree cover (no leaf-type at 10m — generic forest)
    20: ["кустарники/заросли"],   # shrubland
    30: ["луг"],                  # grassland
    40: ["поле/сорное"],          # cropland
    50: ["сады/парки"],           # built-up (urban green proxy)
    60: ["каменистые/скалистые склоны", "пески/дюны/обнажения"],  # bare / sparse veg
    70: [],                       # snow & ice
    80: ["водное/прибрежное"],    # permanent water bodies
    90: ["болото/сырое"],         # herbaceous wetland
    95: [],                       # mangroves
    100: ["горы/предгорья"],      # moss & lichen (tundra/alpine proxy)
}

_open: dict[str, "rasterio.DatasetReader | None"] = {}
_lock = threading.Lock()


def _tile_name(lat: float, lng: float) -> str:
    return "N%02dE%03d" % (3 * math.floor(lat / 3), 3 * math.floor(lng / 3))


def _dataset(lat: float, lng: float):
    name = _tile_name(lat, lng)
    ds = _open.get(name, "miss")
    if ds != "miss":
        return ds
    with _lock:
        if name in _open:
            return _open[name]
        path = os.path.join(_DIR, name + ".tif")
        try:
            _open[name] = rasterio.open(path) if os.path.exists(path) else None
        except Exception:
            _open[name] = None
        return _open[name]


def biotope_at(lat: float, lng: float) -> set[str]:
    """Canonical biotope(s) at the point (WorldCover), or empty set if no tile/no-data.
    Blocking single-pixel read (a few ms) — call via asyncio.to_thread from async code."""
    ds = _dataset(lat, lng)
    if ds is None:
        return set()
    try:
        row, col = ds.index(lng, lat)
        if not (0 <= row < ds.height and 0 <= col < ds.width):
            return set()
        val = int(ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
    except Exception:
        return set()
    return set(_CLASS_BIOTOPE.get(val, []))
