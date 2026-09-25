"""Добор фотографий карточек как задача Temporal.

Проход ходит в iNaturalist по одному запросу в две секунды и занимает часы,
поэтому живёт в воркере диспетчера: heartbeat после каждой карточки, а после
перезапуска работа продолжается сама, потому что обработанные карточки
отмечены в базе (``inat_synced_at``) и в выборку больше не попадают. Логика в
``app/services/photo_backfill.py``.
"""

from __future__ import annotations

import logging

from temporalio import activity

from app.services.photo_backfill import backfill_photos, parse_since

logger = logging.getLogger(__name__)


@activity.defn
async def photo_backfill_activity(since_iso: str, limit: int = 0) -> dict:
    """``since_iso`` это момент старта воркфлоу: карточки, отмеченные позже него,
    уже обработаны этим прогоном. ``limit`` ограничивает число карточек через API
    (пробный запуск, ноль означает все)."""
    counters: dict = {}
    details = activity.info().heartbeat_details
    if details:
        try:
            saved = details[0]
            counters = {k: int(v) for k, v in saved.items() if isinstance(v, (int, float))}
        except Exception:  # noqa: BLE001 — битые детали heartbeat не должны валить прогон
            counters = {}
    if counters:
        logger.info("photo backfill: продолжаем, счётчики %s", counters)

    def report(progress: dict) -> None:
        activity.heartbeat(progress)

    return await backfill_photos(parse_since(since_iso), limit=limit,
                                 progress=report, counters=counters)
