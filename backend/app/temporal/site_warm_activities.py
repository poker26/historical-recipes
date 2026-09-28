"""Прогрев сайта как задача Temporal.

Полный обход карты сайта (десятки тысяч адресов в спокойном темпе) идёт часами, поэтому
живёт в воркере диспетчера. Heartbeat несёт число пройденных адресов: после перезапуска
обход продолжается с того же места. Логика в ``app/services/site_warm.py``.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio import activity

from app.services.site_warm import run_site_warm

logger = logging.getLogger(__name__)


@activity.defn
async def site_warm_activity(limit: int = 0, rps: float = 2.0, only: list[str] | None = None) -> dict:
    """``limit`` ограничивает число адресов для пробного прогона, ``rps`` задаёт темп,
    ``only`` оставляет карты с такими префиксами имени (например ["plants", "recipes"])."""
    skip = 0
    details = activity.info().heartbeat_details
    if details:
        try:
            skip = int(dict(details[0]).get("done", 0))
        except Exception:  # noqa: BLE001 — битые детали heartbeat не должны валить обход
            skip = 0
    if skip:
        logger.info("site warm: продолжаем с адреса %s", skip)

    last: dict = {"done": skip}

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
        # limit считается от начала карты, поэтому при продолжении вычитаем пройденное
        rest = max(0, limit - skip) if limit else 0
        if limit and not rest:
            return {"done": skip, "note": "лимит уже пройден"}
        return await run_site_warm(limit=rest, rps=rps, only=only, skip=skip, progress=report)
    finally:
        tick.cancel()
