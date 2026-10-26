"""«Мой подоконник»: комнатные растения человека и советы книг на этот сезон.

Ключ устройства в пути, как у остальных ручек /api/devices. Растение может быть
любой карточкой атласа, но советы есть только у тех, у кого есть раздел ухода.
"""

import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.windowsill import SEASON_RU, advice_now, season_of

router = APIRouter()

MAX_PLANTS = 100


class AddPlant(BaseModel):
    plant_id: uuid.UUID


class Prefs(BaseModel):
    reminders: bool


async def windowsill_view(db: AsyncSession, device_key: uuid.UUID) -> dict:
    season = season_of(date.today())
    rows = (await db.execute(text("""
        SELECT w.plant_id::text, w.added_at, p.name, p.name_latin, p.photo_url, m.monograph
        FROM windowsill_plants w
        JOIN plants p ON p.id = w.plant_id
        LEFT JOIN plant_reader_monograph m ON m.plant_id = w.plant_id
        WHERE w.device_key = :dk
        ORDER BY w.added_at DESC"""), {"dk": device_key})).all()
    plants = []
    for r in rows:
        mono = r.monograph if isinstance(r.monograph, dict) else (json.loads(r.monograph) if r.monograph else {})
        plants.append({
            "plant_id": r.plant_id,
            "name": mono.get("name") or r.name,
            "name_latin": r.name_latin,
            "photo_url": mono.get("photo_url") or r.photo_url,
            "advice": advice_now(mono, season),
            "added_at": r.added_at.isoformat(),
        })
    pref = (await db.execute(text("SELECT reminders FROM windowsill_prefs WHERE device_key = :dk"),
                             {"dk": device_key})).scalar()
    return {"season": season, "season_ru": SEASON_RU[season], "plants": plants,
            "reminders": True if pref is None else bool(pref)}


@router.get("/{device_key}/windowsill")
async def get_windowsill(device_key: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await windowsill_view(db, device_key)


@router.post("/{device_key}/windowsill")
async def add_plant(device_key: uuid.UUID, body: AddPlant, db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(text("SELECT 1 FROM plants WHERE id = :p"), {"p": body.plant_id})).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="plant not found")
    n = (await db.execute(text("SELECT count(*) FROM windowsill_plants WHERE device_key = :dk"),
                          {"dk": device_key})).scalar()
    if n >= MAX_PLANTS:
        raise HTTPException(status_code=409, detail="windowsill is full")
    await db.execute(text("""
        INSERT INTO windowsill_plants (device_key, plant_id) VALUES (:dk, :p)
        ON CONFLICT DO NOTHING"""), {"dk": device_key, "p": body.plant_id})
    # Отсчёт напоминаний идёт с первого растения: человек только что сам видел
    # советы на этот сезон, и пуш о нём назавтра был бы лишним.
    await db.execute(text("""
        INSERT INTO windowsill_prefs (device_key, last_season, last_reminder_at)
        VALUES (:dk, :s, now()) ON CONFLICT (device_key) DO NOTHING"""),
        {"dk": device_key, "s": season_of(date.today())})
    await db.commit()
    return await windowsill_view(db, device_key)


@router.delete("/{device_key}/windowsill/{plant_id}")
async def remove_plant(device_key: uuid.UUID, plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM windowsill_plants WHERE device_key = :dk AND plant_id = :p"),
                     {"dk": device_key, "p": plant_id})
    await db.commit()
    return await windowsill_view(db, device_key)


@router.put("/{device_key}/windowsill/prefs")
async def set_prefs(device_key: uuid.UUID, body: Prefs, db: AsyncSession = Depends(get_db)):
    await db.execute(text("""
        INSERT INTO windowsill_prefs (device_key, reminders) VALUES (:dk, :r)
        ON CONFLICT (device_key) DO UPDATE SET reminders = EXCLUDED.reminders"""),
        {"dk": device_key, "r": body.reminders})
    await db.commit()
    return {"reminders": body.reminders}
