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

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.identification import Identification
from app.models.plant import Plant
from app.services import minio as minio_svc
from app.services import plant_id
from app.services import mushroom_id

# Hard, non-negotiable warning surfaced on EVERY mushroom identification (HANDOFF-
# identify-improvements Q3): photo-ID has deadly lookalikes (бледная поганка ↔
# шампиньон). Shown ABOVE the result; the per-card safety block carries the level/twin.
_MUSHROOM_DISCLAIMER = (
    "⚠️ Определение грибов по фото НЕ заменяет эксперта. У смертельно ядовитых грибов "
    "есть съедобные двойники. НИКОГДА не употребляйте гриб, опознанный только по фото. "
    "Сверяйтесь с уровнем опасности и «опасным двойником» на карточке."
)
_MUSHROOM_KINGDOMS = {"fungi", "mushroom", "гриб", "грибы"}
from app.services.inaturalist import resolve_names_ru, resolve_registry_photos
from app.services.plant_matching import resolve_latin_to_plants, resolve_latin_to_spine

logger = logging.getLogger(__name__)
router = APIRouter()

# Auto-routing confidence (HANDOFF-identify-improvements Q3 revision): on kingdom=auto
# the user just snaps a photo — the backend decides the kingdom. Run the FREE plant
# engine first; only when it is NOT confident do we spend a PAID Kindwise mushroom
# call. A plant top-score ≥ _PLANT_CONFIDENT short-circuits (no mushroom call); a
# mushroom result is accepted only at top-score ≥ _MUSH_CONFIDENT. Tunable.
_PLANT_CONFIDENT = 0.30
_MUSH_CONFIDENT = 0.50


def _confident(result: dict, threshold: float) -> bool:
    """A non-error engine result whose TOP candidate scores ≥ threshold."""
    if not result or result.get("error"):
        return False
    cands = result.get("candidates") or []
    return bool(cands) and (cands[0].get("score") or 0) >= threshold


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
    unmatched_latins: list[str] = []
    for c in candidates:
        p = by_latin.get(c["latin"])
        c["plant"] = _plant_card(p) if p is not None else None
        if p is not None:
            matched += 1
        else:
            unmatched_latins.append(c["latin"])
    # A candidate NOT in our corpus would otherwise display PlantNet's English
    # common name; resolve a Russian name (and iNat taxon_id) from iNaturalist so
    # corpus and non-corpus hits read consistently in Russian. Corpus candidates
    # already carry a Russian name (plant.name / name_modern), so skip those.
    if unmatched_latins:
        names_ru = await resolve_names_ru(db, unmatched_latins)
        for c in candidates:
            info = names_ru.get(c["latin"]) if c["plant"] is None else None
            if info:
                c["name_ru"] = info.get("name_ru")
                c["inat_taxon_id"] = info.get("taxon_id")
        # Registry-as-completeness (no stub Plant rows): a candidate we lack a monograph
        # for but that IS in the Cherepanov backbone is «known FSU flora, monograph
        # pending» — not «not in corpus». Attach `registry` {status, accepted_latin,
        # family_latin} so the client says so instead of a dead end.
        spine = await resolve_latin_to_spine(db, unmatched_latins)
        # Enrich the registry hit with a license-clean iNat photo + RU name (lazy, cached)
        # — no LLM, no invented facts: just «known species, here's how it looks».
        registry_latins = [c["latin"] for c in candidates
                           if c.get("plant") is None and c["latin"] in spine]
        photos = await resolve_registry_photos(db, registry_latins) if registry_latins else {}
        registry = 0
        for c in candidates:
            if c.get("plant") is None and c["latin"] in spine:
                reg = dict(spine[c["latin"]])
                ph = photos.get(c["latin"])
                if ph:
                    reg["photo_url"] = ph.get("photo_url")
                    reg["photo_attribution"] = ph.get("photo_attribution")
                    if not c.get("name_ru") and ph.get("common_name"):
                        c["name_ru"] = ph.get("common_name")
                c["registry"] = reg
                registry += 1
        if registry:
            result["registry_count"] = registry
    result["matched_count"] = matched
    return result


def _parse_captured_at(raw: str | None) -> datetime | None:
    """Parse the client's capture timestamp leniently — ISO 8601 or epoch
    seconds/millis. Returns None on anything unparseable (the field is optional)."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        v = float(raw)
        if v > 1e12:  # milliseconds
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    """Lenient UUID parse — the device_key the client sends with each identify.
    Returns None on absent/invalid so a bad value never breaks archival."""
    if not raw:
        return None
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError):
        return None


async def _archive(db: AsyncSession, *, photo: bytes | None, result: dict,
                   organs: list[str] | None, lat, lng, geo_accuracy, captured_at,
                   exif_json, device_model, device_manufacturer, os_version,
                   os_sdk, app_version, device_key=None) -> None:
    """Archive the photo (→ field-uploads bucket) + its metadata + the engine
    outcome to the ``identifications`` table. Best-effort: any failure is logged
    and swallowed so archival never breaks the user's identification."""
    try:
        photo_key = None
        if photo:
            key = f"uploads/{datetime.now(timezone.utc):%Y/%m}/{uuid.uuid4().hex}.jpg"
            minio_svc.ensure_bucket(settings.minio_field_bucket)
            minio_svc.upload_file(photo, key, content_type="image/jpeg",
                                  bucket=settings.minio_field_bucket)
            photo_key = key

        candidates = result.get("candidates") or []
        top = candidates[0] if candidates else {}
        matched_plant_id = next(
            (c["plant"]["id"] for c in candidates if c.get("plant")), None
        )
        exif = None
        if exif_json:
            try:
                exif = json.loads(exif_json)
            except (ValueError, TypeError):
                exif = {"_raw": exif_json[:2000]}

        db.add(Identification(
            photo_key=photo_key,
            device_key=_parse_uuid(device_key),
            engine=result.get("engine"),
            organ=(organs[0] if organs else None),
            lat=lat, lng=lng, geo_accuracy=geo_accuracy,
            captured_at=_parse_captured_at(captured_at),
            exif=exif,
            device_model=device_model,
            device_manufacturer=device_manufacturer,
            os_version=os_version,
            os_sdk=os_sdk,
            app_version=app_version,
            top_latin=top.get("latin"),
            top_score=top.get("score"),
            matched_plant_id=uuid.UUID(matched_plant_id) if matched_plant_id else None,
            matched_count=result.get("matched_count"),
            remaining_requests=result.get("remaining_requests"),
            candidates=candidates or None,
        ))
        await db.commit()
    except Exception as e:
        logger.warning(f"identification archive failed: {type(e).__name__}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


@router.post("/")
async def identify_plant(
    images: list[UploadFile] = File(...),
    organs: list[str] | None = Form(None),
    limit: int = Form(5),
    # Kingdom routing (HANDOFF-identify-improvements Q3 revision). Default «auto»:
    # the client no longer asks the user «is it a mushroom?» — the backend tries the
    # plant engine and falls back to the fungi engine only if plant is weak. «plant»
    # / «fungi» remain as explicit force-overrides.
    kingdom: str = Form("auto"),
    # Field-capture metadata (all optional). Geolocation is only sent when the
    # user granted the permission; EXIF is read by the client from the ORIGINAL
    # photo (the uploaded frame is recompressed and loses it).
    lat: float | None = Form(None),
    lng: float | None = Form(None),
    geo_accuracy: float | None = Form(None),
    captured_at: str | None = Form(None),
    exif_json: str | None = Form(None),
    device_model: str | None = Form(None),
    device_manufacturer: str | None = Form(None),
    os_version: str | None = Form(None),
    os_sdk: int | None = Form(None),
    app_version: str | None = Form(None),
    # Silent-identity device id (same UUID as /devices/register). Required for
    # quest badge progress — identifications.device_key feeds badge_progress.
    device_key: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Identify a plant from one or more uploaded photos and link each candidate
    species to our herbarium.

    Form fields: ``images`` (1–5 JPG/PNG files), optional ``organs`` (one per
    image: leaf/flower/fruit/bark/auto; a single value applies to all), ``limit``
    (max candidates, default 5). Optional field-capture metadata
    (``lat``/``lng``/``geo_accuracy``, ``captured_at``, ``exif_json``,
    ``device_model``/``device_manufacturer``/``os_version``/``os_sdk``/
    ``app_version``) is archived alongside the photo for debugging.

    Returns ``{engine, candidates:[{latin, score, common_names, gbif_id, …,
    plant: card|null}], matched_count, remaining_requests}``. Each matched
    ``plant.id`` is the key for GET /api/plants/{id}."""
    blobs = [await f.read() for f in images]
    mode = (kingdom or "auto").strip().lower()
    if mode in _MUSHROOM_KINGDOMS:                  # forced fungi
        result = await mushroom_id.identify(blobs, organs=organs, limit=limit)
        is_mushroom = True
    elif mode == "plant":                           # forced plant
        result = await plant_id.identify(blobs, organs=organs, limit=limit)
        is_mushroom = False
    else:                                           # auto: plant first, fungi only if plant is weak
        result = await plant_id.identify(blobs, organs=organs, limit=limit)
        is_mushroom = False
        if not _confident(result, _PLANT_CONFIDENT):
            shroom = await mushroom_id.identify(blobs, organs=organs, limit=limit)
            if _confident(shroom, _MUSH_CONFIDENT):
                result, is_mushroom = shroom, True
            # else keep the (weak/empty) plant result → honest «не определилось»
    result = await _bridge(result, db)
    if is_mushroom:
        result["safety_notice"] = _MUSHROOM_DISCLAIMER   # always when the verdict is fungi
    await _archive(
        db, photo=(blobs[0] if blobs else None), result=result, organs=organs,
        lat=lat, lng=lng, geo_accuracy=geo_accuracy, captured_at=captured_at,
        exif_json=exif_json, device_model=device_model,
        device_manufacturer=device_manufacturer, os_version=os_version,
        os_sdk=os_sdk, app_version=app_version, device_key=device_key,
    )
    return result


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
