"""Заливка слоя комнатных растений как задача Temporal.

Разбор книги идёт часами и переживает деплой только в воркере: активность бьёт
heartbeat после каждого куска, и при перезапуске прогон продолжается с того
места, где остановился, а не с начала. Ровно этого не хватало, когда заливки
запускались через ``docker compose exec`` и умирали от пересборки backend.
"""

import logging

from temporalio import activity

from app.services.houseplant_ingest import ingest_book
from app.services.houseplant_toxicity import ingest_toxicity_book

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


@activity.defn
async def houseplant_toxicity_activity(source: str, book: str, limit: int = 0) -> dict:
    """Разбирает пособие по ядовитым комнатным: чем опасно, симптомы, первая помощь.

    Возобновляемая так же, как заливка ухода: номер разобранной статьи уходит в
    heartbeat, после перезапуска работа продолжается с него.
    """
    start_at = 0
    details = activity.info().heartbeat_details
    if details:
        try:
            start_at = int(details[0])
        except (TypeError, ValueError):
            start_at = 0
    if start_at:
        logger.info(f"houseplant toxicity {source}: продолжаем со статьи {start_at}")

    async def report(progress: dict) -> None:
        activity.heartbeat(progress.get("done", 0) if progress.get("stage") == "разбор" else 0,
                           progress)

    return await ingest_toxicity_book(source, book, limit=limit, start_at=start_at,
                                      apply=True, on_piece=report)


@activity.defn
async def houseplant_cards_activity(min_facts: int = 3, limit: int = 0) -> dict:
    """Пересобирает карточки комнатных растений из слоя ухода и опасности.

    Сборка детерминированная, без вызовов модели, но идёт сотнями запросов к
    iNaturalist за фотографиями, поэтому живёт в воркере, а не в ssh-сессии.
    """
    from app.database import async_session
    from app.services.houseplant_cards import build_cards

    async with async_session() as db:
        out = await build_cards(db, limit=limit, min_facts=min_facts)
    activity.heartbeat(out.get("cards", 0), out)
    return out


@activity.defn
async def card_latin_cleanup_activity(limit: int = 0, apply: bool = True) -> dict:
    """Снимает фамилии ботаников с латинских имён карточек.

    Работа идёт сотнями запросов к GBIF: каждое имя проверяется справочником,
    и без подтверждения карточка остаётся как была. Из-за этих запросов прогон
    живёт в воркере, а не в ssh-сессии.
    """
    from app.database import async_session
    from app.services.card_latin import clean_card_latins

    async def report(progress: dict) -> None:
        activity.heartbeat(progress.get("done", 0), progress)

    async with async_session() as db:
        return await clean_card_latins(db, limit=limit, apply=apply, on_piece=report)


@activity.defn
async def card_photos_activity(limit: int = 0, apply: bool = True) -> dict:
    """Добирает карточкам комнатных фотографии из Викимедиа.

    Сотни запросов к Википедии по одному на карточку, поэтому прогон живёт в
    воркере. Возобновляемая: повторный запуск берёт только те карточки, у
    которых снимка всё ещё нет.
    """
    from app.database import async_session
    from app.services.wikimedia import fill_card_photos

    async def report(progress: dict) -> None:
        activity.heartbeat(progress.get("done", 0), progress)

    async with async_session() as db:
        return await fill_card_photos(db, limit=limit, apply=apply, on_piece=report)


@activity.defn
async def card_merge_activity(apply: bool = True) -> dict:
    """Сводит карточки-дубли, разведённые грязным латинским именем.

    Работа целиком в базе, но затрагивает тождество карточек, поэтому идёт
    прогоном с отчётом, а не запросом из сессии.
    """
    from app.database import async_session
    from app.services.card_merge import merge_houseplant_duplicates

    async def report(progress: dict) -> None:
        activity.heartbeat(progress.get("done", 0), progress)

    async with async_session() as db:
        return await merge_houseplant_duplicates(db, apply=apply, on_piece=report)
