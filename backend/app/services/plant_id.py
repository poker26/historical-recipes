"""Photo → species identification via the Pl@ntNet API.

This is the *engine* layer of the "identify a plant from a photo" feature. It is
deliberately thin and swappable: it returns a normalized list of candidate
species (latin binomial + score + ids), and nothing here knows about our corpus.
The bridge to our herbarium happens one layer up (the router), keyed on the latin
name via ``plant_matching._latin_key`` — the SAME key used for herbarium dedup,
so an engine result "Gratiola officinalis L." matches our stored row.

Why Pl@ntNet for the first engine: its free tier (500 ids/day) is enough for an
MVP, and its response carries ``scientificNameWithoutAuthor`` (a clean binomial)
plus the GBIF id — exactly the bridge key we already index on. The engine is held
behind ``identify()`` so a future swap (Plant.id/Kindwise, an LLM re-ranker) is a
one-file change.

Like ``inaturalist.py`` this never raises: every failure path returns a dict with
an ``error`` field so the API degrades instead of 500-ing.
"""

import asyncio
import logging

import httpx

from app.config import settings
from app.services.egress import hop_client, parse_hops

logger = logging.getLogger(__name__)

# Pl@ntNet accepts at most 5 images per identification request.
# Сколько раз пробуем достучаться до движка, когда рвётся сам туннель, и какая
# пауза между попытками. Три попытки с секундой и двумя перекрывают моргание
# связи и не заставляют человека ждать дольше десяти секунд.
PROXY_ATTEMPTS = 4
PROXY_PAUSE = 1.0

MAX_IMAGES = 5
# Organ tags the engine understands; "auto" lets it detect per image.
VALID_ORGANS = {"leaf", "flower", "fruit", "bark", "auto"}


def _normalize_results(body: dict, *, limit: int) -> list[dict]:
    """Shape Pl@ntNet's ``results`` into our engine-neutral candidate contract.

    Each candidate: ``latin`` (clean binomial — the bridge key), ``latin_author``
    (with author citation), ``score`` (0–1), ``common_names``, ``genus``,
    ``family``, ``gbif_id``, ``powo_id``. Missing fields degrade to None."""
    out: list[dict] = []
    for r in (body.get("results") or [])[:limit]:
        species = r.get("species") or {}
        gbif = r.get("gbif") or {}
        powo = r.get("powo") or {}
        latin = species.get("scientificNameWithoutAuthor")
        if not latin:
            continue
        out.append({
            "latin": latin,
            "latin_author": species.get("scientificName") or species.get("scientificNameWithoutAuthor"),
            "score": r.get("score"),
            "common_names": species.get("commonNames") or [],
            "genus": (species.get("genus") or {}).get("scientificNameWithoutAuthor"),
            "family": (species.get("family") or {}).get("scientificNameWithoutAuthor"),
            "gbif_id": gbif.get("id"),
            "powo_id": powo.get("id"),
        })
    return out


async def identify(
    images: list[bytes],
    *,
    organs: list[str] | None = None,
    image_urls: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """Identify a plant from one or more photos.

    Provide EITHER raw image ``images`` (bytes, multipart-uploaded to the engine)
    OR remote ``image_urls`` (passed as URL query params — the path the MCP tool
    uses, since MCP args are JSON, not file uploads). ``organs`` parallels the
    images (leaf/flower/fruit/bark/auto); a single value applies to all, and a
    missing/short list is padded with "auto".

    Returns ``{engine, candidates:[…], remaining_requests}`` on success, or
    ``{error: …}`` on any failure (no key configured, transport error, non-200,
    bad body). Never raises."""
    if not settings.plantnet_api_key:
        return {"error": "Pl@ntNet API key not configured (set PLANTNET_API_KEY)"}

    sources = images or image_urls
    if not sources:
        return {"error": "no images provided"}
    sources = sources[:MAX_IMAGES]

    # Pad/normalize organs to one per image; unknown tags fall back to "auto".
    organs = list(organs or [])
    if len(organs) == 1:
        organs = organs * len(sources)
    organs = (organs + ["auto"] * len(sources))[: len(sources)]
    organs = [o if o in VALID_ORGANS else "auto" for o in organs]

    url = f"{settings.plantnet_base_url.rstrip('/')}/identify/{settings.plantnet_project}"
    params = {"api-key": settings.plantnet_api_key, "nb-results": min(max(limit, 1), 10)}

    # Одной попытки мало. 21 сентября туннель до PlantNet моргал с девяти утра до
    # шести вечера: соединение не устанавливалось на части запросов, и 151 снимок
    # уехал в архив с отказом — человек в этот день просто не получил ответа.
    # Порт при этом был открыт, поэтому и мониторинг молчал. Транспортную ошибку
    # пробуем пережить: связь возвращается за секунды, а снимок уже у нас.
    last_error: Exception | None = None
    resp = None
    # Сначала путь по умолчанию, затем запасные выходы по очереди: первый
    # ответивший и работает.
    hops: list = [None]
    if settings.plantnet_proxy and settings.plantnet_proxy_hops:
        hostname = httpx.URL(settings.plantnet_proxy).host
        hops += parse_hops(settings.plantnet_proxy_hops, hostname)

    for attempt in range(PROXY_ATTEMPTS):
        hop = hops[attempt % len(hops)]
        try:
            # Route through the configured proxy when set (prod egress to PlantNet's
            # host is network-blocked; the trusttunnel proxy provides the path).
            async with hop_client(settings.plantnet_proxy, hop, timeout=60) as client:
                if images:
                    files = [("images", (f"img{i}.jpg", b, "image/jpeg")) for i, b in enumerate(sources)]
                    # httpx wants form fields as a dict; a list value emits repeated
                    # `organs` parts. Passing a list-of-tuples here makes httpx 0.28
                    # treat `data` as raw content (sync stream) and the AsyncClient
                    # rejects it ("sync request with an AsyncClient instance").
                    data = {"organs": organs}
                    resp = await client.post(url, params=params, files=files, data=data)
                else:
                    # Remote-URL path: images + organs are repeated query params.
                    q = [("api-key", settings.plantnet_api_key),
                         ("nb-results", str(min(max(limit, 1), 10)))]
                    q += [("images", u) for u in sources]
                    q += [("organs", o) for o in organs]
                    resp = await client.get(url, params=q)
            break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
            last_error = e
            if attempt + 1 < PROXY_ATTEMPTS:
                where = f"{hop.host}:{hop.port}" if hop else "путь по умолчанию"
                logger.warning(f"Pl@ntNet {type(e).__name__} через {where}, попытка "
                               f"{attempt + 1} из {PROXY_ATTEMPTS}, пробуем следующий выход")
                await asyncio.sleep(PROXY_PAUSE * (attempt + 1))
                continue
        except httpx.HTTPError as e:
            last_error = e
            break

    if resp is None:
        logger.warning(f"Pl@ntNet request failed: {type(last_error).__name__}: {last_error}")
        return {"error": f"identification engine request failed: {last_error}"}

    if resp.status_code == 404:
        # Pl@ntNet returns 404 when it can't match the photo to any species.
        return {"engine": "plantnet", "candidates": [], "note": "no species matched the photo"}
    if resp.status_code == 401:
        return {"error": "Pl@ntNet rejected the API key (401)"}
    if resp.status_code == 429:
        return {"error": "Pl@ntNet daily quota exceeded (429)"}
    if resp.status_code != 200:
        return {"error": f"identification engine HTTP {resp.status_code}", "detail": resp.text[:300]}

    try:
        body = resp.json()
    except ValueError as e:
        return {"error": f"identification engine returned non-JSON body: {e}"}

    return {
        "engine": "plantnet",
        "candidates": _normalize_results(body, limit=limit),
        "remaining_requests": body.get("remainingIdentificationRequests"),
    }
