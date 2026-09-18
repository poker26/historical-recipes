"""Заливка слоя комнатных растений как задача Temporal.

Разбор книги идёт часами и переживает деплой только в воркере: активность бьёт
heartbeat после каждого куска, и при перезапуске прогон продолжается с того
места, где остановился, а не с начала. Ровно этого не хватало, когда заливки
запускались через ``docker compose exec`` и умирали от пересборки backend.
"""

import logging

from temporalio import activity

from app.services.houseplant_ingest import ingest_book

logger = logging.getLogger(__name__)


@activity.defn
async def houseplant_ingest_activity(source: str, by_genus: bool = False,
                                     pages: list[int] | None = None,
                                     limit: int = 0) -> dict:
    """Разбирает книгу слоя комнатных и пишет уход в базу.

    Возобновляемая: номер последнего разобранного куска уезжает в heartbeat, и
    после перезапуска активность начинает со следующего. Повторная запись
    безопасна — цитата одного источника второй раз в базу не ложится.
    """
    start_at = 0
    details = activity.info().heartbeat_details
    if details:
        try:
            start_at = int(details[0])
        except (TypeError, ValueError):
            start_at = 0
    if start_at:
        logger.info(f"houseplant ingest {source}: продолжаем с куска {start_at}")

    async def report(progress: dict) -> None:
        activity.heartbeat(progress.get("done", 0), progress)

    return await ingest_book(source, by_genus=by_genus, pages=pages, limit=limit,
                             start_at=start_at, apply=True, on_piece=report)
