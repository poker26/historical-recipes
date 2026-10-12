"""Общие квесты «Общий подоконник» и «Общий лес» (docs/RFC-collective-quests.md).

Смонтирован под /api/quests/collective: этот префикс уже открыт для приложения в nginx
(flora), отдельного правила не нужно. Проверка жалоб на фото лежит в /api/moderation.
"""
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import collective_quests as cq
from app.services import minio as minio_svc

router = APIRouter()

# Путь к фото участника относительный: клиент добавляет к нему свой адрес API.
PHOTO_BASE = "/api/quests/collective/photo"


@router.get("")
async def collective_state(device_key: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    """Оба открытых квеста с позициями, баллы пользователя и последние собранные квесты.
    Попутно заменяет застрявшие позиции и открывает следующий квест, если прежний собран."""
    return await cq.state(db, device_key, PHOTO_BASE)


@router.get("/photo/{slot_id}.jpg")
async def collective_photo(slot_id: str, db: AsyncSession = Depends(get_db)):
    """Фото участника для «Общего леса»: уменьшенная копия без EXIF, место съёмки не уходит.
    Для «Общего подоконника», скрытых по жалобе и удалённых снимков отвечает 404."""
    try:
        uuid.UUID(slot_id)
    except ValueError:
        raise HTTPException(404)
    key = await cq.user_photo(db, slot_id)
    if not key:
        raise HTTPException(404)
    try:
        raw = minio_svc.get_client().get_object(settings.minio_field_bucket, key)
        try:
            data = raw.read()
        finally:
            raw.close()
            raw.release_conn()
    except Exception:
        raise HTTPException(404)
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((1080, 1080))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)      # новая запись без EXIF
    return Response(out.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


class ReportIn(BaseModel):
    slot_id: str
    device_key: str


@router.post("/report")
async def collective_report(body: ReportIn, db: AsyncSession = Depends(get_db)):
    """Жалоба на фото участника. Фото скрывается сразу, вернуть его можно в модерации."""
    try:
        return await cq.report_photo(db, body.slot_id, body.device_key)
    except ValueError:
        raise HTTPException(400, "bad slot_id or device_key")
