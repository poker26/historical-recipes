"""plantarium.ru — Russia-focused REGIONAL floristic checklist for Layer-2 of custom
quests (RFC-custom-quests, Phase 4).

Used ONLY as a «known in this region» signal to filter biotope-characteristic species
where local point-data (iNat/GBIF) is empty — its geo is region-coarse, so it's a
regional presence list, not point occurrences. Licence (user-checked): photos are the
authors' (we take NONE — names/facts only), text/lists are reusable WITH attribution,
and MASS scraping is forbidden → we fetch ONE region's list on demand per quest, never
crawl. Always surface the `source_url` so the client can credit plantarium.ru.
"""
import asyncio
import re
import time

import httpx

PLANTARIUM = "https://www.plantarium.ru"
_UA = "Mozilla/5.0 (compatible; chto-rastet-quest/1.0; +https://botanik.fun)"

# A species row: ...name="iNNN">Genus species</a> <span class="taxon-author …
_SPECIES_RE = re.compile(r'>([A-Z][a-zë]+ [a-zë×-]+)</a>\s*<span class="taxon-author')
# Directory entry: /page/flora/id/NNN.html"...>Region name</a>
_DIR_RE = re.compile(r'/page/flora/id/(\d+)\.html"[^>]*>([^<]{3,})</a>')

# Cached directory [(name_lower, id)] — one fetch of the index, reused across quests.
_directory_cache: list[tuple[str, str]] = []

_NARROW = ("памятник", "заказник", "урочищ", "природы", " бор", "гора", "озеро",
           "балка", "степи", "лес ", "роща", "парк")
_BROAD = ("список сосудистых", "област", "край", "республик", "флора ")


async def _directory(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    if _directory_cache:
        return _directory_cache
    try:
        r = await client.get(f"{PLANTARIUM}/page/floras.html")
        if r.status_code == 200:
            for fid, nm in _DIR_RE.findall(r.text):
                _directory_cache.append((nm.strip().lower(), fid))
    except httpx.HTTPError:
        pass
    return _directory_cache


def _best_list_for(region: str, directory: list[tuple[str, str]]) -> str | None:
    """Among directory entries matching the region, prefer the BROADEST list (whole
    oblast «список сосудистых растений …»), not a tiny памятник-природы reserve."""
    root = region.strip().lower().split()[0][:6] if region.strip() else ""
    if not root:
        return None
    best, best_score = None, -10
    for nm, fid in directory:
        if root not in nm:
            continue
        score = sum(2 for b in _BROAD if b in nm) - sum(1 for w in _NARROW if w in nm)
        if score > best_score:
            best, best_score = fid, score
    return best


async def region_species(region_name: str | None, max_pages: int = 5) -> tuple[set[str], str | None]:
    """Latin binomials from the plantarium floristic list best matching `region_name`.
    On-demand, ONE region per call (no mass crawl). Returns (latins, source_url|None)."""
    if not region_name:
        return set(), None
    async with httpx.AsyncClient(timeout=40, headers={"User-Agent": _UA}) as client:
        directory = await _directory(client)
        fid = _best_list_for(region_name, directory)
        if not fid:
            return set(), None
        base = f"{PLANTARIUM}/page/flora/id/{fid}.html"
        latins: set[str] = set()
        for part in range(max_pages):
            url = base if part == 0 else f"{PLANTARIUM}/page/flora/id/{fid}/part/{part}.html"
            try:
                r = await client.get(url)
            except httpx.HTTPError:
                break
            if r.status_code != 200:
                break
            hits = _SPECIES_RE.findall(r.text)
            if not hits:
                break
            latins.update(h.strip() for h in hits)
        return latins, (base if latins else None)


async def region_at(lat: float, lng: float) -> str | None:
    """Admin region (oblast/край/республика) at the point — OSM Nominatim reverse."""
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA}) as client:
            r = await client.get("https://nominatim.openstreetmap.org/reverse", params={
                "lat": lat, "lon": lng, "format": "json", "accept-language": "ru", "zoom": 8})
            if r.status_code == 200:
                a = (r.json() or {}).get("address", {}) or {}
                return a.get("state") or a.get("region") or a.get("county")
    except (httpx.HTTPError, ValueError):
        pass
    return None


# Address keys that name a walkable green destination (use as the quest name as-is) vs
# a nearby locality (combined with the biotope: «Лес · Сосновка»). OSM names — no
# moderation needed (they're real place names, not user free-text). `county` is the
# rural fallback (Nominatim gives only county/state in the middle of fields/taiga).
_GREEN_KEYS = ("leisure", "park", "garden", "forest", "wood",
               "nature_reserve", "protected_area")
_LOCALITY_KEYS = ("neighbourhood", "suburb", "quarter", "city_district", "residential",
                  "hamlet", "village", "town", "municipality", "city", "county")
# Admin boilerplate to strip so «городской округ Серпухов» → «Серпухов».
_ADMIN_NOISE = re.compile(
    r"\b(городской округ|муниципальный округ|муниципальный район|городское поселение|"
    r"сельское поселение|сельский округ|городской район|район)\b", re.IGNORECASE)


def _clean_locality(s: str) -> str:
    return re.sub(r"\s+", " ", _ADMIN_NOISE.sub("", s)).strip(" -·,")


# Политика Nominatim — не чаще одного запроса в секунду с одного клиента. Массовое
# заведение личных мест (33 штуки подряд) её нарушило и получило 429 на все точки,
# из-за чего часть мест осталась без топонима. Держим очередь сами: не быстрее
# 1.2 с между запросами + память ответов по точке (округление до ~100 м).
_NOM_GAP_S = 1.2
_nom_lock = asyncio.Lock()
_nom_last = 0.0
_nom_cache: dict[tuple, dict | None] = {}


async def toponym_at(lat: float, lng: float) -> dict | None:
    """Nearest meaningful place name at the point (OSM Nominatim reverse) for auto-naming
    a custom quest. Returns {green, locality} (either may be None) — `green` = a named
    park/forest to use directly, `locality` = nearest settlement/neighbourhood (admin
    boilerplate stripped) to pair with the biotope. None on failure (caller falls back to
    the biotope name)."""
    key = (round(lat, 3), round(lng, 3))
    if key in _nom_cache:
        return _nom_cache[key]
    try:
        async with _nom_lock:                     # один запрос за раз, не чаще 1/1.2 с
            global _nom_last
            wait = _NOM_GAP_S - (time.monotonic() - _nom_last)
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA}) as client:
                r = await client.get("https://nominatim.openstreetmap.org/reverse", params={
                    "lat": lat, "lon": lng, "format": "jsonv2",
                    "accept-language": "ru", "zoom": 16})
                if r.status_code == 429:          # уже под ограничением — подождать и разок повторить
                    await asyncio.sleep(5)
                    r = await client.get("https://nominatim.openstreetmap.org/reverse", params={
                        "lat": lat, "lon": lng, "format": "jsonv2",
                        "accept-language": "ru", "zoom": 16})
                _nom_last = time.monotonic()
            if r.status_code != 200:
                return None                       # 429/5xx НЕ кешируем: это не ответ, а отказ
            j = r.json() or {}
            a = j.get("address", {}) or {}
            green = next((a[k] for k in _GREEN_KEYS if a.get(k)), None)
            # The matched feature itself may be the named green spot (a forest/park polygon),
            # but NOT an admin boundary or a street — those aren't walk destinations.
            if not green and j.get("category") in ("leisure", "natural") and j.get("name"):
                green = j["name"]
            loc_raw = next((a[k] for k in _LOCALITY_KEYS if a.get(k)), None)
            locality = _clean_locality(loc_raw) if loc_raw else None
            if green or locality:
                _nom_cache[key] = {"green": green, "locality": locality or None}
                return _nom_cache[key]
            _nom_cache[key] = None                # тут действительно нет имени
    except (httpx.HTTPError, ValueError):
        pass
    return None
