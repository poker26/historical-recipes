"""Год издания и автор книг как задача Temporal.

Прогон спрашивает модель по каждой книге и идёт часами, поэтому живёт в воркере
диспетчера. Heartbeat уходит после каждой книги и ещё раз в минуту сам по себе:
ответ модели по сканам иногда ждёт несколько минут, и прогон не должен считаться
зависшим. После перезапуска работа продолжается сама, потому что разобранная
книга получает находку ``book.meta`` и в выборку больше не попадает. Логика в
``app/services/book_meta.py``.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio import activity

from app.services.book_meta import run_book_meta

logger = logging.getLogger(__name__)


@activity.defn
async def book_meta_activity(apply: bool = False, limit: int = 0, force: bool = False) -> dict:
    """``apply`` записывает надёжное сразу (иначе только находки), ``limit`` ограничивает
    число книг для пробного прогона, ``force`` разбирает заново и книги с находкой."""
    counters: dict = {}
    details = activity.info().heartbeat_details
    if details:
        try:
            counters = {k: int(v) for k, v in dict(details[0]).items() if isinstance(v, (int, float))}
        except Exception:  # noqa: BLE001 — битые детали heartbeat не должны валить прогон
            counters = {}
    if counters:
        logger.info("book meta: продолжаем, счётчики %s", counters)

    last: dict = dict(counters)

    def report(progress: dict) -> None:
        nonlocal last
        last = progress
        activity.heartbeat(progress)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(60)
            activity.heartbeat(last)

    tick = asyncio.create_task(ticker())
    try:
        return await run_book_meta(apply=apply, limit=limit, force=force,
                                   progress=report, counters=counters)
    finally:
        tick.cancel()
