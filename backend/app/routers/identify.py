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

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.identification import Identification
from app.models.plant import Plant
from app.services import minio as minio_svc
from app.services import plant_id
from app.services import mushroom_id
from app.services import quests as quests_svc

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
from app.services.plant_matching import (
    resolve_latin_to_plants, resolve_latin_to_spine, resolve_genus_relatives,
    synonym_map, canonical_key, _latin_key,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Auto-routing confidence (HANDOFF-identify-improvements Q3 revision): on kingdom=auto
# the user just snaps a photo — the backend decides the kingdom. Run the FREE plant
# engine first; only when it is NOT confident do we spend a PAID Kindwise mushroom
# call. A plant top-score ≥ _PLANT_CONFIDENT short-circuits (no mushroom call); a
# mushroom result is accepted only at top-score ≥ _MUSH_CONFIDENT. Tunable.
_PLANT_CONFIDENT = 0.30
# КАЛИБРОВАННЫЙ порог для нашего движка (mushroom_id._calibrate). 0.41 = косинус
# 0.85 по замеру 2026-08-25 (120 фото грибов vs 120 полевых фото растений):
# принимает 78% настоящих грибов и лишь 3% растений. Старое значение 0.50 было
# унаследовано от Kindwise, где score = ВЕРОЯТНОСТЬ; у косинуса же оно проходило
# ВСЕГДА — и любое не опознанное PlantNet растение объявлялось грибом (баг,
# найденный Олегом). Порог поднимать = меньше ложных грибов, но больше пропусков.
_MUSH_CONFIDENT = 0.41

# Границы КЛАССИФИКАТОРА ЦАРСТВА (логрегрессия на эмбеддинге BioCLIP-2, отдаётся
# движком как p_fungus). Замер на отложенных реальных фото 2026-08-25: на 0.5 —
# 94.7% грибов и 94.7% растений верно. Мы не берём одну границу, а оставляем
# «полосу сомнения»: выше _KINGDOM_FUNGI — точно гриб (96% растений сюда не
# попадают), ниже _KINGDOM_PLANT — точно растение (98% грибов сюда не попадают),
# между ними решает уверенность самих движков. Это заменяет прежнюю лестницу
# «сначала растение, если слабо — гриб», которая на косинусе давала ложные грибы.
_KINGDOM_FUNGI = 0.70
_KINGDOM_PLANT = 0.30


async def _auto_route(blobs, *, organs, limit) -> tuple[dict, bool]:
    """kingdom=auto: спросить ОБА движка разом и показать ответ того царства,
    которое назвал классификатор.

    Раньше здесь была лестница: сначала PlantNet, и только если он не уверен —
    грибной движок, чей ответ принимался по порогу уверенности. Порог оказался
    хрупким (косинус против вероятности — баг «все растения стали грибами»), да и
    сама постановка неверна: «растение не опознано» ≠ «это гриб». Теперь царство
    определяется отдельно и до кандидатов, по эмбеддингу того же снимка.

    Оба вызова идут параллельно, поэтому ответ не медленнее прежнего. В полосе
    сомнения (0.30–0.70) решает уверенность движков — при равных грибной ответ
    принимается только с порога _MUSH_CONFIDENT, как и раньше."""
    plant, shroom = await asyncio.gather(
        plant_id.identify(blobs, organs=organs, limit=limit),
        mushroom_id.identify(blobs, organs=organs, limit=limit),
    )
    p_fungus = shroom.get("p_fungus") if isinstance(shroom, dict) else None

    if p_fungus is None:                       # движок без классификатора → старая лестница
        if _confident(plant, _PLANT_CONFIDENT):
            return plant, False
        return (shroom, True) if _confident(shroom, _MUSH_CONFIDENT) else (plant, False)

    if p_fungus >= _KINGDOM_FUNGI:
        return (shroom, True) if (shroom.get("candidates") or []) else (plant, False)
    if p_fungus <= _KINGDOM_PLANT:
        return plant, False
    # полоса сомнения: верим тому, кто увереннее в своём царстве
    if _confident(plant, _PLANT_CONFIDENT):
        return plant, False
    return (shroom, True) if _confident(shroom, _MUSH_CONFIDENT) else (plant, False)


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
    syn = await synonym_map(db)
    matched = 0
    unmatched_latins: list[str] = []
    for c in candidates:
        p = by_latin.get(c["latin"])
        c["plant"] = _plant_card(p) if p is not None else None
        # Canonical accepted-name key, so the client can tell «same species» even across
        # genus synonyms (Betonica↔Stachys officinalis) or duplicate corpus cards.
        c["species_key"] = canonical_key(_latin_key(c["latin"]), syn)
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
        # Реестр теперь покрывает ВСЕ виды вне корпуса, а не только черепановскую
        # флору. Смысл (формулировка Олега 2026-08-26): ответ должен звучать не
        # «мы не смогли определить», а «мы знаем, что это — просто у атласа другая
        # задача». Поэтому фото и русское имя тянем для любого неопознанного вида,
        # а `status` объясняет ПОЧЕМУ очерка нет:
        #   accepted / synonym — своя флора, очерк ещё не написан;
        #   outside_scope      — вид определён, но он вне рамок травника
        #                        (комнатные, клумбовые, чужая флора) — 70% отказов.
        photos = await resolve_registry_photos(db, unmatched_latins)
        registry = out_of_scope = 0
        for c in candidates:
            if c.get("plant") is not None:
                continue
            reg = dict(spine.get(c["latin"]) or {"status": "outside_scope"})
            ph = photos.get(c["latin"])
            if ph:
                reg["photo_url"] = ph.get("photo_url")
                reg["photo_attribution"] = ph.get("photo_attribution")
                if not c.get("name_ru") and ph.get("common_name"):
                    c["name_ru"] = ph.get("common_name")
            c["registry"] = reg
            if reg["status"] == "outside_scope":
                out_of_scope += 1
            else:
                registry += 1
        if registry:
            result["registry_count"] = registry
        if out_of_scope:
            result["out_of_scope_count"] = out_of_scope
        # Третий ярус после карточки и реестра: РОД. Две трети отказов (замер
        # 2026-08-26) — это виды, чей род в корпусе есть; вместо тупика «нет в
        # атласе» отдаём родню, честно помеченную отдельным полем. Подменять вид
        # родственником в поле `plant` НЕЛЬЗЯ — снят Crassula ovata, а показать
        # Тиллею водную как «его карточку» было бы враньём.
        relatives = await resolve_genus_relatives(db, unmatched_latins)
        genus_hits = 0
        for c in candidates:
            if c.get("plant") is None and c["latin"] in relatives:
                c["genus_relatives"] = relatives[c["latin"]]
                genus_hits += 1
        if genus_hits:
            result["genus_count"] = genus_hits
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
        # Причина отказа (миграция 021): ошибка движка и пустой ответ — разные беды.
        failure_reason = failure_detail = None
        if result.get("error"):
            failure_reason, failure_detail = "engine_error", str(result["error"])[:400]
        elif not candidates:
            failure_reason = "no_candidates"
        top_score = top.get("score") or 0
        # matched_plant_id = the card of the TOP candidate, or a NEAR-TIE (score ≥ 85% of
        # top) — NEVER a low-score also-ran. A 0.002 sibling once hijacked matched_plant_id:
        # a Буквица shot (top = Stachys officinalis, no card) got mislinked to the carded
        # «Вероника колосистая» further down the list.
        matched_plant_id = next(
            (c["plant"]["id"] for c in candidates
             if c.get("plant") and (c.get("score") or 0) >= top_score * 0.85),
            None,
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
            failure_reason=failure_reason,
            failure_detail=failure_detail,
        ))
        # Touch quest_devices.last_seen: register is called once per install, so
        # without this «active devices» undercounts ~2× (146 identifying devices
        # vs 80 by last_seen over the same 30 days, measured 2026-08-24).
        dk = _parse_uuid(device_key)
        if dk is not None:
            await db.execute(
                sa_text("UPDATE quest_devices SET last_seen = now() "
                        "WHERE device_key = CAST(:dk AS uuid)"), {"dk": str(dk)})
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
    else:                                           # auto: царство решает классификатор
        result, is_mushroom = await _auto_route(blobs, organs=organs, limit=limit)
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
    # Quest credit for THIS shot (place quest containing the point, current window):
    # ties the shooting moment to the game — most identifying devices never open the
    # quests tab, so progress made in-place must be visible right on the result
    # screen. Optional field; old clients ignore it (ignoreUnknownKeys).
    if device_key and lat is not None and lng is not None and result.get("candidates"):
        try:
            cands = result["candidates"]
            qc = await quests_svc.quest_credit_for_shot(
                db, device_key, lat, lng, cands,
                cands[0].get("latin"), cands[0].get("score"))
            if qc:
                result["quest_credit"] = qc
        except Exception as e:
            logger.warning(f"quest_credit skipped: {type(e).__name__}: {e}")
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
