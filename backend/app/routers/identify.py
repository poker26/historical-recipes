"""Photo → species identification, bridged to our herbarium.

Pipeline: a user photo goes to the identification engine (``services.plant_id``,
Pl@ntNet for now), which returns candidate latin binomials with confidence; each
candidate is then resolved to our ``Plant`` via the genus+species latin key
(``plant_matching.resolve_latin_to_plants`` — the SAME key as herbarium dedup).
A matched candidate carries a ``plant`` card whose ``id`` the caller feeds to
``GET /api/plants/{id}`` (or the MCP ``get_plant``) for the full source-grounded
monograph + iNat enrichment. An unmatched candidate (real species we don't have a
monograph for yet) returns ``plant: null`` — also a signal of which book to ingest.

The engine is the only swappable part; everything below the candidate list is
engine-neutral.
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.plant import Plant
from app.services import plant_id
from app.services.plant_matching import resolve_latin_to_plants

router = APIRouter()


def _plant_card(p: Plant) -> dict:
    """Minimal identity card for a matched plant — enough to display the hit and
    to fetch the full monograph via get_plant(id)."""
    return {
        "id": str(p.id),
        "name": p.name,
        "name_latin": p.name_latin,
        "name_modern": p.name_modern,
        "family": p.family,
        "is_toxic": p.is_toxic,
        "kingdom": p.kingdom,
        "photo_url": p.photo_url,
        "photo_attribution": p.photo_attribution,
    }


async def _bridge(result: dict, db: AsyncSession) -> dict:
    """Attach a herbarium ``plant`` card to each engine candidate by latin key.

    Mutates and returns the engine ``result``. On an engine error or empty
    candidate list it is a no-op passthrough. Also surfaces ``matched_count`` so a
    caller can tell at a glance whether any candidate is in our corpus."""
    candidates = result.get("candidates") or []
    if not candidates:
        return result
    by_latin = await resolve_latin_to_plants(db, [c["latin"] for c in candidates])
    matched = 0
    for c in candidates:
        p = by_latin.get(c["latin"])
        c["plant"] = _plant_card(p) if p is not None else None
        if p is not None:
            matched += 1
    result["matched_count"] = matched
    return result


@router.post("/")
async def identify_plant(
    images: list[UploadFile] = File(...),
    organs: list[str] | None = Form(None),
    limit: int = Form(5),
    db: AsyncSession = Depends(get_db),
):
    """Identify a plant from one or more uploaded photos and link each candidate
    species to our herbarium.

    Form fields: ``images`` (1–5 JPG/PNG files), optional ``organs`` (one per
    image: leaf/flower/fruit/bark/auto; a single value applies to all), ``limit``
    (max candidates, default 5).

    Returns ``{engine, candidates:[{latin, score, common_names, gbif_id, …,
    plant: card|null}], matched_count, remaining_requests}``. Each matched
    ``plant.id`` is the key for GET /api/plants/{id}."""
    blobs = [await f.read() for f in images]
    result = await plant_id.identify(blobs, organs=organs, limit=limit)
    return await _bridge(result, db)


class IdentifyByUrlRequest(BaseModel):
    image_urls: list[str]
    organs: list[str] | None = None
    limit: int = 5


@router.post("/by-url")
async def identify_plant_by_url(
    req: IdentifyByUrlRequest,
    db: AsyncSession = Depends(get_db),
):
    """Identify a plant from remote image URLs (the JSON path used by the MCP
    ``identify_plant`` tool, since MCP args can't carry file uploads).

    Body: ``{image_urls:[…1–5], organs?:[…], limit?:int}``. Same response shape as
    POST /api/identify."""
    result = await plant_id.identify([], image_urls=req.image_urls, organs=req.organs, limit=req.limit)
    return await _bridge(result, db)
