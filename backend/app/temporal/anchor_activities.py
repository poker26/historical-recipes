"""Привязка фактов корпуса к страницам сканов как задача Temporal.

Прогон идёт по всем книгам и по всем таблицам фактов и занимает часы, поэтому
живёт в воркере диспетчера: после каждого куска уходит heartbeat с номером
последней законченной книги, и после перезапуска работа продолжается с неё.
Внутри книги повторный проход безопасен: берутся только строки, у которых
способ поиска ещё не записан.

Сама логика поиска страницы лежит в ``app/services/page_anchor.py``. Она
считает на процессоре, поэтому каждый кусок уходит в отдельный поток, а цикл
событий воркера остаётся свободным для heartbeat и соседних активностей.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text
from temporalio import activity

from app.database import async_session
from app.services.page_anchor import BookIndex, locate

logger = logging.getLogger(__name__)

# (таблица, колонка книги, колонка текста, колонка страницы)
FACT_TABLES = [
    ("plant_medicinal_uses", "source_book_id", "original_text", "source_page"),
    ("recipes", "book_id", "original_text", "source_page"),
    ("plant_culinary_uses", "source_book_id", "original_text", "source_page"),
    ("plant_harvests", "source_book_id", "original_text", "source_page"),
    ("plant_habitats", "source_book_id", "original_text", "source_page"),
    ("plant_toxicities", "source_book_id", "original_text", "source_page"),
    ("essential_oil_uses", "source_book_id", "original_text", "source_page"),
    ("plant_book_mentions", "book_id", "original_text", "page_number"),
]

CHUNK = 400          # строк за один поход в базу и один кусок счёта
BOOKS_PER_QUERY = 25


def _empty_counters() -> dict:
    return {"books": 0, "rows": 0, "exact": 0, "fuzzy": 0, "none": 0, "short": 0, "nopages": 0}


async def _load_pages(book_id: uuid.UUID) -> list[tuple[int, str | None]]:
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT page_number, raw_text FROM book_pages "
            "WHERE book_id = :b AND raw_text IS NOT NULL ORDER BY page_number"),
            {"b": book_id})).all()
    return [(int(n), t) for n, t in rows]


async def _anchor_table(book_id: uuid.UUID, index: BookIndex | None, table: str,
                        book_col: str, text_col: str, page_col: str,
                        counters: dict, progress) -> None:
    """Привязывает все ещё не обработанные факты одной таблицы для одной книги."""
    while True:
        async with async_session() as db:
            rows = (await db.execute(text(
                f"SELECT id, {text_col} FROM {table} "
                f"WHERE {book_col} = :b AND anchor_method IS NULL AND {text_col} IS NOT NULL "
                f"ORDER BY id LIMIT :n"), {"b": book_id, "n": CHUNK})).all()
        if not rows:
            return

        if index is None:
            updates = [{"id": rid, "page": None, "score": 0, "method": "nopages"} for rid, _ in rows]
        else:
            anchors = await asyncio.to_thread(lambda: [locate(index, t) for _, t in rows])
            updates = [{"id": rid, "page": a.page, "score": a.score, "method": a.method}
                       for (rid, _), a in zip(rows, anchors)]

        async with async_session() as db:
            await db.execute(text(
                f"UPDATE {table} SET {page_col} = :page, anchor_score = :score, "
                f"anchor_method = :method WHERE id = :id"), updates)
            await db.commit()

        for u in updates:
            counters["rows"] += 1
            counters[u["method"]] = counters.get(u["method"], 0) + 1
        progress()
        if len(rows) < CHUNK:
            return


@activity.defn
async def page_anchor_activity(limit: int = 0) -> dict:
    """Проходит книги по порядку идентификаторов и привязывает факты к страницам.

    ``limit`` ограничивает число книг за прогон (пробный запуск). Ноль означает
    весь корпус. Курсор возобновления это идентификатор последней полностью
    обработанной книги; он уезжает в heartbeat вместе со счётчиками.
    """
    info = activity.info()
    last_book = ""
    counters = _empty_counters()
    if info.heartbeat_details:
        try:
            saved = info.heartbeat_details[0]
            last_book = saved.get("last_book", "") or ""
            for k in counters:
                counters[k] = int(saved.get(k, 0) or 0)
        except Exception:  # noqa: BLE001 — битые детали heartbeat не должны валить прогон
            last_book = ""
    if last_book:
        logger.info("page anchor: продолжаем после книги %s", last_book)

    def progress() -> None:
        activity.heartbeat({"last_book": last_book, **counters})

    processed_this_run = 0
    while True:
        async with async_session() as db:
            books = (await db.execute(text(
                "SELECT id FROM books WHERE id::text > :cur ORDER BY id::text LIMIT :n"),
                {"cur": last_book, "n": BOOKS_PER_QUERY})).scalars().all()
        if not books:
            break
        for book_id in books:
            if limit and processed_this_run >= limit:
                return {"phase": "anchor", "stopped_by_limit": True, "last_book": last_book, **counters}
            raw_pages = await _load_pages(book_id)
            index = await asyncio.to_thread(BookIndex.build, raw_pages) if raw_pages else None
            if index is not None and not index.pages:
                index = None
            for table, book_col, text_col, page_col in FACT_TABLES:
                await _anchor_table(book_id, index, table, book_col, text_col, page_col,
                                    counters, progress)
            last_book = str(book_id)
            counters["books"] += 1
            processed_this_run += 1
            progress()
            await asyncio.sleep(0)
    return {"phase": "anchor", "stopped_by_limit": False, "last_book": last_book, **counters}
