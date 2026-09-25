"""Добор фотографий карточек из iNaturalist: медленный, возобновляемый проход.

Зачем отдельный проход, если добор уже есть (``enrich_plants_inat``). Старый
проход брал только «главное фото» таксона и отбрасывал его, если лицензия не
свободная; так 1 105 карточек с найденным таксоном остались без фото, хотя у
таксона в iNaturalist обычно десяток фото с разными лицензиями. Кроме того,
старый проход остановили в июне из-за шторма ошибок 429 и не возобновили:
2 460 видовых карточек с латынью в iNaturalist вообще не ходили. Родовые
карточки (700 без фото) фото не получали никогда: их латынь это один род без
вида, и резолвер их отказывался искать.

Проход делает три вещи, в порядке отдачи:

1. Родовые карточки получают фото одного из своих видов, без единого запроса
   к API. Источник помечается ``inaturalist:member``, чтобы сайт мог подписать
   «фото вида из этого рода».
2. Карточки с известным таксоном и без фото: один запрос ``GET /v1/taxa/{id}``,
   из ``taxon_photos`` выбирается фото с самой свободной лицензией.
3. Карточки с латынью, в iNaturalist ещё не ходившие: резолвер таксона, как в
   старом проходе (таксон, современное русское имя), а если у главного фото
   лицензия не подходит, второй запрос за списком фото таксона.

Порядок внутри фазы: сначала карточки, до которых люди доходили через
определение за последние 90 дней, потом карточки с готовым очерком, потом по
имени. Темп один запрос в две секунды: iNaturalist просит не больше шестидесяти
в минуту, и половину бюджета оставляем живым запросам приложения.

Идемпотентность и возобновление. Каждая обработанная карточка получает
``inat_synced_at = now()``. Фаза 2 берёт карточки, у которых отметка старше
начала прогона (``since``), фаза 3 те, у которых отметки нет. После падения
воркфлоу продолжает с того же запроса без списка сделанного. Временный отказ
API (обрыв, 429 после бэкоффа) отметку не ставит, и карточка попадёт в
следующий прогон.

Сохранённые очерки Слоя 2 хранят поля фото внутри своего JSON, и приложение
показывает именно их. Поэтому вместе с карточкой обновляется и очерк.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
from sqlalchemy import text

from app.database import async_session
from app.services.data_quality.validators.name_junk import clean_display_name
from app.services.inaturalist import (
    COMMERCIAL_OK_LICENSES, DISPLAY_OK_LICENSES, INAT_BASE, _HEADERS,
    _ICONIC_FOR_KINGDOM, _has_cyrillic, resolve_taxon_photo,
)

logger = logging.getLogger(__name__)

PACE_SECONDS = 2.0
BATCH = 200
# Столько подряд временных отказов означает, что iNaturalist прижимает нас
# всерьёз; лучше остановиться и дать следующему прогону начать заново.
MAX_TRANSIENT_STREAK = 12

# Чем свободнее лицензия, тем раньше в списке. Сначала те, что годятся и для
# коммерческого сайта, потом остальные Creative Commons (как в старом проходе).
_LICENSE_ORDER = ["cc0", "cc-by", "cc-by-sa", "cc-by-nd", "cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd"]

Progress = Callable[[dict], None]


def pick_licensed_photo(taxon_photos: list[dict]) -> dict | None:
    """Выбирает из ``taxon_photos`` фото с самой свободной лицензией.

    Возвращает ``{photo_url, photo_attribution, photo_license}`` или ``None``,
    если ни у одного фото нет лицензии Creative Commons.
    """
    best: dict | None = None
    best_rank = len(_LICENSE_ORDER)
    for tp in taxon_photos or []:
        photo = tp.get("photo") or {}
        code = (photo.get("license_code") or "").lower()
        if code not in DISPLAY_OK_LICENSES:
            continue
        rank = _LICENSE_ORDER.index(code) if code in _LICENSE_ORDER else len(_LICENSE_ORDER) - 1
        url = photo.get("medium_url") or photo.get("url")
        if not url:
            continue
        if rank < best_rank:
            best_rank = rank
            best = {"photo_url": url, "photo_attribution": photo.get("attribution"), "photo_license": code}
            if rank == 0:
                break
    return best


async def fetch_taxon_photos(client: httpx.AsyncClient, taxon_id: int) -> list[dict] | None:
    """Список фото таксона. ``None`` означает временный отказ API (повторим в
    другой раз), пустой список означает, что фото у таксона нет."""
    for attempt in range(4):
        try:
            resp = await client.get(f"{INAT_BASE}/taxa/{int(taxon_id)}", headers=_HEADERS)
        except (httpx.HTTPError, ValueError) as e:
            # Обрыв соединения («Server disconnected») у iNaturalist бывает и на
            # здоровом сервере; одна короткая пауза и повтор снимают большую часть.
            logger.warning(f"iNat taxa/{taxon_id} error: {type(e).__name__}: {e} (attempt {attempt+1}/4)")
            if attempt < 3:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            return None
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if (retry_after or "").isdigit() else (5 * (attempt + 1))
            logger.warning(f"iNat 429 for taxa/{taxon_id}; backing off {delay}s (attempt {attempt+1}/4)")
            await asyncio.sleep(delay)
            continue
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            logger.warning(f"iNat taxa/{taxon_id} HTTP {resp.status_code}")
            return None
        try:
            results = resp.json().get("results") or []
        except ValueError:
            return None
        if not results:
            return []
        return results[0].get("taxon_photos") or []
    return None


# ---------------------------------------------------------------- запись в базу

async def _store_photo(plant_id, photo: dict, source: str, taxon_id: int | None = None,
                       name_modern: str | None = None, promote_name: bool = False) -> None:
    """Пишет фото в карточку и в сохранённый очерк одной транзакцией."""
    async with async_session() as db:
        sets = ["photo_url = :u", "photo_attribution = :a", "photo_license = :l",
                "photo_source = :s", "inat_synced_at = now()"]
        params = {"u": photo["photo_url"], "a": photo.get("photo_attribution"),
                  "l": photo.get("photo_license"), "s": source, "id": plant_id}
        if taxon_id:
            sets.append("inat_taxon_id = :t")
            params["t"] = int(taxon_id)
        if name_modern:
            sets.append("name_modern = :nm")
            params["nm"] = name_modern
            if promote_name:
                sets.append("name = :nm")
        await db.execute(text(f"UPDATE plants SET {', '.join(sets)} WHERE id = :id"), params)
        await db.execute(text(
            "UPDATE plant_reader_monograph SET monograph = monograph || jsonb_build_object("
            "'photo_url', CAST(:u AS text), 'photo_attribution', CAST(:a AS text), "
            "'photo_license', CAST(:l AS text), 'photo_source', CAST(:s AS text)) "
            "WHERE plant_id = :id"), params)
        await db.commit()


async def _mark_synced(plant_id, taxon_id: int | None = None,
                       name_modern: str | None = None, promote_name: bool = False) -> None:
    """Отмечает попытку без фото: таксон и имя (если нашлись) сохраняем, фото нет."""
    async with async_session() as db:
        sets = ["inat_synced_at = now()"]
        params = {"id": plant_id}
        if taxon_id:
            sets.append("inat_taxon_id = :t")
            params["t"] = int(taxon_id)
        if name_modern:
            sets.append("name_modern = :nm")
            params["nm"] = name_modern
            if promote_name:
                sets.append("name = :nm")
        await db.execute(text(f"UPDATE plants SET {', '.join(sets)} WHERE id = :id"), params)
        await db.commit()


# ---------------------------------------------------------------- фазы

async def inherit_genus_photos() -> int:
    """Родовые карточки без фото берут фото одного из своих видов. Один SQL."""
    async with async_session() as db:
        res = await db.execute(text("""
            WITH pick AS (
                SELECT DISTINCT ON (g.id) g.id AS genus_id, s.photo_url, s.photo_attribution,
                       s.photo_license
                FROM plants g
                JOIN plants s ON s.parent_id = g.id AND s.photo_url IS NOT NULL
                WHERE g.rank = 'genus' AND g.photo_url IS NULL
                  AND g.kingdom IN ('растение', 'гриб')
                ORDER BY g.id,
                         (s.photo_license IN ('cc0', 'cc-by', 'cc-by-sa')) DESC,
                         (SELECT count(*) FROM identifications i WHERE i.matched_plant_id = s.id) DESC,
                         s.name
            )
            UPDATE plants p SET photo_url = pick.photo_url, photo_attribution = pick.photo_attribution,
                   photo_license = pick.photo_license, photo_source = 'inaturalist:member'
            FROM pick WHERE p.id = pick.genus_id
        """))
        n = res.rowcount or 0
        await db.execute(text("""
            UPDATE plant_reader_monograph m SET monograph = m.monograph || jsonb_build_object(
                'photo_url', p.photo_url, 'photo_attribution', p.photo_attribution,
                'photo_license', p.photo_license, 'photo_source', p.photo_source)
            FROM plants p WHERE p.id = m.plant_id AND p.photo_source = 'inaturalist:member'
        """))
        await db.commit()
    return n


_REACH_ORDER = """
    ORDER BY coalesce(r.c, 0) DESC,
             (EXISTS (SELECT 1 FROM plant_reader_monograph m WHERE m.plant_id = p.id AND m.reviewed)) DESC,
             p.name
    LIMIT :n"""

_REACH_JOIN = """
    LEFT JOIN (SELECT matched_plant_id AS pid, count(*) AS c FROM identifications
               WHERE created_at > now() - interval '90 days' GROUP BY 1) r ON r.pid = p.id"""


async def _batch_known_taxon(since: datetime, exclude: set) -> list[tuple]:
    async with async_session() as db:
        rows = (await db.execute(text(f"""
            SELECT p.id, p.name, p.inat_taxon_id FROM plants p {_REACH_JOIN}
            WHERE p.kingdom IN ('растение', 'гриб') AND p.photo_url IS NULL
              AND p.inat_taxon_id IS NOT NULL
              AND (p.inat_synced_at IS NULL OR p.inat_synced_at < :since)
            {_REACH_ORDER}"""), {"since": since, "n": BATCH})).all()
    return [r for r in rows if r[0] not in exclude]


async def _batch_untried(exclude: set) -> list[tuple]:
    async with async_session() as db:
        rows = (await db.execute(text(f"""
            SELECT p.id, p.name, p.name_latin, p.kingdom FROM plants p {_REACH_JOIN}
            WHERE p.kingdom IN ('растение', 'гриб') AND p.photo_url IS NULL
              AND p.name_latin IS NOT NULL AND p.inat_synced_at IS NULL
              AND p.rank = 'species'
            {_REACH_ORDER}"""), {"n": BATCH})).all()
    return [r for r in rows if r[0] not in exclude]


async def backfill_photos(since: datetime, limit: int = 0,
                          pace_seconds: float = PACE_SECONDS,
                          progress: Progress | None = None,
                          counters: dict | None = None) -> dict:
    """Весь проход. ``limit`` ограничивает число карточек, обработанных через API
    (пробный запуск); родовое наследование в лимит не входит."""
    c = counters if counters is not None else {}
    for k in ("genus", "known_done", "known_photo", "untried_done", "untried_taxon",
              "untried_photo", "no_match", "transient", "requests"):
        c.setdefault(k, 0)

    def report(phase: str, name: str = "") -> None:
        if progress is not None:
            try:
                progress({"phase": phase, "name": name, **c})
            except Exception:  # noqa: BLE001 — телеметрия не должна ронять прогон
                pass

    c["genus"] += await inherit_genus_photos()
    report("genus")

    api_done = 0
    streak = 0
    seen: set = set()
    # Без keep-alive: iNaturalist закрывает простаивающие соединения, и повторное
    # использование закрытого даёт «Server disconnected». При темпе в две секунды
    # новое соединение на запрос ничего не стоит.
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        # Фаза 2: таксон известен, фото нет.
        while not (limit and api_done >= limit):
            rows = await _batch_known_taxon(since, seen)
            if not rows:
                break
            for pid, name, taxon_id in rows:
                if limit and api_done >= limit:
                    break
                seen.add(pid)
                photos = await fetch_taxon_photos(client, taxon_id)
                c["requests"] += 1
                api_done += 1
                if photos is None:
                    c["transient"] += 1
                    streak += 1
                else:
                    streak = 0
                    picked = pick_licensed_photo(photos)
                    if picked:
                        await _store_photo(pid, picked, "inaturalist")
                        c["known_photo"] += 1
                    else:
                        await _mark_synced(pid)
                    c["known_done"] += 1
                report("known_taxon", name)
                if streak >= MAX_TRANSIENT_STREAK:
                    return {**c, "stopped": "transient_streak"}
                await asyncio.sleep(pace_seconds)

        # Фаза 3: латынь есть, в iNaturalist не ходили.
        while not (limit and api_done >= limit):
            rows = await _batch_untried(seen)
            if not rows:
                break
            for pid, name, latin, kingdom in rows:
                if limit and api_done >= limit:
                    break
                seen.add(pid)
                iconic = _ICONIC_FOR_KINGDOM.get(kingdom or "растение", "Plantae")
                res = await resolve_taxon_photo(client, latin, iconic=iconic)
                c["requests"] += 1
                api_done += 1
                if res is None:
                    c["transient"] += 1
                    streak += 1
                    report("untried", name)
                    if streak >= MAX_TRANSIENT_STREAK:
                        return {**c, "stopped": "transient_streak"}
                    await asyncio.sleep(pace_seconds)
                    continue
                streak = 0
                c["untried_done"] += 1
                taxon_id = res.get("taxon_id")
                if not taxon_id:
                    c["no_match"] += 1
                    await _mark_synced(pid)
                    report("untried", name)
                    await asyncio.sleep(pace_seconds)
                    continue
                c["untried_taxon"] += 1
                common = res.get("common_name")
                name_modern = (clean_display_name(common) or common) if _has_cyrillic(common) else None
                promote = bool(name_modern) and not _has_cyrillic(name)
                picked = None
                if res.get("photo_url"):
                    picked = {"photo_url": res["photo_url"], "photo_attribution": res.get("photo_attribution"),
                              "photo_license": res.get("photo_license")}
                else:
                    await asyncio.sleep(pace_seconds)
                    photos = await fetch_taxon_photos(client, taxon_id)
                    c["requests"] += 1
                    if photos is None:
                        # Временный отказ на втором запросе: таксон запомним, отметку не ставим,
                        # карточка вернётся в следующий прогон фазой 2 (таксон есть, фото нет).
                        c["transient"] += 1
                        streak += 1
                        async with async_session() as db:
                            await db.execute(text("UPDATE plants SET inat_taxon_id = :t WHERE id = :id"),
                                             {"t": int(taxon_id), "id": pid})
                            await db.commit()
                        report("untried", name)
                        if streak >= MAX_TRANSIENT_STREAK:
                            return {**c, "stopped": "transient_streak"}
                        await asyncio.sleep(pace_seconds)
                        continue
                    if photos:
                        picked = pick_licensed_photo(photos)
                if picked:
                    await _store_photo(pid, picked, "inaturalist", taxon_id=taxon_id,
                                       name_modern=name_modern, promote_name=promote)
                    c["untried_photo"] += 1
                else:
                    await _mark_synced(pid, taxon_id=taxon_id, name_modern=name_modern, promote_name=promote)
                report("untried", name)
                await asyncio.sleep(pace_seconds)

    return {**c, "stopped": "limit" if (limit and api_done >= limit) else "done"}


def parse_since(since_iso: str) -> datetime:
    dt = datetime.fromisoformat(since_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
