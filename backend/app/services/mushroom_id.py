"""Photo → mushroom identification via Kindwise Mushroom.id (engine layer, swappable).

Pl@ntNet (``plant_id.py``) is plants-only — a mushroom photo returns 404 / garbage
flora, never reaching the (correct) fungi bridge. This engine covers fungi behind the
SAME contract as ``plant_id.identify`` → ``{engine, candidates:[{latin, score, …}]}``,
so the router's ``_bridge`` (engine-neutral, keyed on the latin binomial via
``_latin_key``) attaches our гриб-monograph and ``kingdom='гриб'`` flows through.

⚠️ SAFETY: photo-ID of mushrooms has DEADLY lookalikes (бледная поганка ↔ шампиньон).
The router attaches the forager-safety block (level + deadly_twin) and a hard
«не употреблять по фото» disclaimer for the mushroom path — fungi are safety-classified.

Like the other engine layers this never raises: every failure returns an ``error`` dict.
"""
import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Kindwise accepts up to 5 images per identification.
MAX_IMAGES = 5


def _normalize(body: dict, *, limit: int) -> list[dict]:
    """Shape Kindwise suggestions into our engine-neutral candidate contract:
    ``latin`` (binomial — the bridge key), ``score`` (0–1), ``common_names``, …."""
    sugg = (((body.get("result") or {}).get("classification") or {}).get("suggestions") or [])
    out: list[dict] = []
    for s in sugg[:limit]:
        latin = s.get("name")
        if not latin:
            continue
        det = s.get("details") or {}
        out.append({
            "latin": latin,
            "latin_author": latin,
            "score": s.get("probability"),
            "common_names": det.get("common_names") or [],
            "genus": None,
            "family": None,
            "gbif_id": det.get("gbif_id"),
            "powo_id": None,
        })
    return out


async def identify(
    images: list[bytes],
    *,
    image_urls: list[str] | None = None,
    limit: int = 5,
    **_ignore,
) -> dict:
    """Identify a mushroom from photos (raw ``images`` bytes, or remote ``image_urls``).
    Returns ``{engine, candidates:[…]}`` on success, ``{error: …}`` on any failure
    (no key, transport error, non-200, bad body). Never raises."""
    if not settings.kindwise_api_key:
        return {"error": "Kindwise API key not configured (set KINDWISE_API_KEY)"}
    imgs = images[:MAX_IMAGES]
    if not imgs and not image_urls:
        return {"error": "no images provided"}

    # NOTE: `similar_images` is a query-style modifier on this v1 API and only
    # `=true` is accepted; we don't want similar-image payloads, so omit it entirely
    # (sending `false` → HTTP 400 "Unknown modifier").
    payload: dict = {}
    if imgs:
        payload["images"] = [base64.b64encode(b).decode("ascii") for b in imgs]
    else:
        payload["images"] = (image_urls or [])[:MAX_IMAGES]
    params = {"details": "common_names,gbif_id", "language": "ru"}
    try:
        async with httpx.AsyncClient(timeout=60, proxy=settings.kindwise_proxy or None) as client:
            r = await client.post(
                f"{settings.kindwise_base_url}/identification",
                params=params, json=payload,
                headers={"Api-Key": settings.kindwise_api_key, "Content-Type": "application/json"})
        if r.status_code not in (200, 201):
            return {"error": f"Kindwise Mushroom.id {r.status_code}: {r.text[:160]}"}
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("mushroom.id transport error: %s", str(e)[:100])
        return {"error": f"Kindwise transport error: {str(e)[:120]}"}
    return {"engine": "mushroom.id", "candidates": _normalize(body, limit=limit)}
