"""Заливка ухода из книги: одно ядро для командной строки и для Temporal.

Разбор книги идёт часами, и запускать его через ``docker compose exec`` нельзя:
такая задача живёт внутри контейнера backend и умирает от любой его пересборки.
18 сентября 2026 так пропали две заливки подряд. Долгие прогоны в проекте
делает Temporal, поэтому ядро вынесено сюда, а вокруг него две тонкие обёртки:
скрипт для ручного запуска и активность воркера.

Прогон возобновляемый и идемпотентный. Возобновляемый — потому что ядро
рассказывает о каждом разобранном куске наружу, и активность отбивает этим
heartbeat; после перезапуска работа продолжается с последнего куска.
Идемпотентный — потому что одна и та же цитата одного источника второй раз в
базу не ложится (ограничение ``uq_houseplant_care_quote``).
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable

import fitz
from sqlalchemy import text

from app.database import async_session
from app.services.houseplant_care import (
    HOUSEPLANT_BOOKS,
    article_has_care,
    extract_article,
    find_genus_articles,
    resolve_latin,
)
from app.services.minio import get_client

BUCKET = "houseplant-books"

# Крупные сканы приходится уменьшать. Головкин снят с разрешением около 1800
# точек на дюйм, и страница, отрисованная в 300, весит столько, что зрячая
# модель отказывается её принимать («Multimodal file size is too large»), а
# распознавание молча остаётся на голом tesseract. Поэтому масштаб считается
# от самой страницы: длинная сторона не длиннее 2200 точек.
MAX_PAGE_SIDE = 2200


def page_pixmap(page, dpi: int = 300):
    """Отрисовывает страницу так, чтобы она прошла к зрячей модели."""
    rect = page.rect
    longest = max(rect.width, rect.height) * dpi / 72
    if longest > MAX_PAGE_SIDE:
        dpi = max(120, int(dpi * MAX_PAGE_SIDE / longest))
    return page.get_pixmap(dpi=dpi)


def open_book(book: str) -> "fitz.Document":
    """Достаёт книгу из хранилища и открывает её."""
    response = get_client().get_object(BUCKET, book)
    try:
        return fitz.open(stream=io.BytesIO(response.read()), filetype="pdf")
    finally:
        response.close()
        response.release_conn()


async def page_text(doc: "fitz.Document", number: int) -> str:
    """Текст страницы; без текстового слоя страница распознаётся."""
    body = doc[number - 1].get_text().strip()
    if len(body) > 200:
        return body

    from app.services.ocr import ocr_page_with_fallback

    pixmap = page_pixmap(doc[number - 1])
    recognized, _confidence, _engine = await ocr_page_with_fallback(pixmap.tobytes("png"))
    return recognized


async def save_source(source_id: str, book: str) -> None:
    """Заводит запись о книге, если её ещё нет."""
    meta = dict(HOUSEPLANT_BOOKS.get(source_id) or {})
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO houseplant_source (id, title, author, year, minio_object, audience)
            VALUES (:id, :title, :author, :year, :object, :audience)
            ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title,
                                           author = EXCLUDED.author,
                                           year = EXCLUDED.year,
                                           minio_object = EXCLUDED.minio_object,
                                           audience = EXCLUDED.audience
        """), {
            "id": source_id,
            "title": meta.get("title") or source_id,
            "author": meta.get("author"),
            "year": meta.get("year"),
            "object": meta.get("object") or book,
            "audience": meta.get("audience") or "room",
        })
        await db.commit()


async def save_plant(source_id: str, plant: dict) -> tuple[int, int]:
    """Пишет уход и болезни одного растения. Возвращает, сколько строк легло."""
    taxon = plant["taxon"]
    latin = (taxon.get("latin") or "").strip()
    russian = (taxon.get("ru") or "").strip()
    rank = (taxon.get("rank") or "genus").strip()

    # Без латыни строку записать некуда: русское имя у книг расходится
    # («плющ восковой» у Саакова, «хойя» у всех остальных), и склеить по нему
    # голоса разных книг нельзя.
    if not latin:
        return 0, 0

    # Имя сверяется с GBIF до записи: иначе один род расползётся по нескольким
    # написаниям, а карточка покажет половину того, что книги про него говорят.
    latin, verified = await resolve_latin(latin)

    care_rows = 0
    problem_rows = 0
    async with async_session() as db:
        for fact in plant["care"]:
            result = await db.execute(text("""
                INSERT INTO houseplant_care
                    (taxon_latin, taxon_rank, taxon_ru, field, season, value, value_text,
                     quote, source_id, page, greenhouse, reference, latin_verified)
                VALUES (:latin, :rank, :ru, :field, :season, CAST(:value AS jsonb), :value_text,
                        :quote, :source_id, :page, :greenhouse, :reference, :verified)
                ON CONFLICT DO NOTHING
            """), {
                "latin": latin, "rank": rank, "ru": russian,
                "field": fact.field_name, "season": fact.season,
                "value": json.dumps(fact.value, ensure_ascii=False),
                "value_text": fact.value_text, "quote": fact.quote,
                "source_id": source_id, "page": fact.page,
                "greenhouse": fact.greenhouse, "reference": fact.reference,
                "verified": verified,
            })
            care_rows += result.rowcount or 0

        for problem in plant["problems"]:
            await db.execute(text("""
                INSERT INTO houseplant_problem
                    (taxon_latin, kind, name, symptom, cause, remedy, chemicals,
                     quote, source_id, page)
                VALUES (:latin, :kind, :name, :symptom, :cause, :remedy, :chemicals,
                        :quote, :source_id, :page)
            """), {
                "latin": latin, "kind": problem.kind, "name": problem.name,
                "symptom": problem.symptom, "cause": problem.cause,
                "remedy": problem.remedy, "chemicals": problem.chemicals,
                "quote": problem.quote, "source_id": source_id, "page": problem.page,
            })
            problem_rows += 1
        await db.commit()
    return care_rows, problem_rows


async def book_pieces(doc: "fitz.Document", by_genus: bool,
                      numbers: list[int]) -> AsyncIterator[tuple[str, int, str]]:
    """Отдаёт куски книги по одному: текст, страница и род из заголовка.

    Кусок отдаётся сразу, как только готов, а не после того, как подготовится
    вся книга: у Воронцова предварительное распознавание всех страниц уводило
    первые полчаса в тишину, а обрыв терял всю работу.
    """
    if by_genus:
        pages = [doc[n - 1].get_text() for n in range(1, doc.page_count + 1)]
        for article in find_genus_articles(pages):
            if article.page in numbers and article_has_care(article):
                yield article.text, article.page, article.genus_latin
        return

    # У книги либо есть текстовый слой, либо нет. Если есть, страницы без
    # текста — это вклейки с фотографиями, и распознавать их незачем.
    with_text = sum(1 for n in numbers if len(doc[n - 1].get_text().strip()) > 200)
    has_text_layer = with_text >= len(numbers) // 2

    for number in numbers:
        if has_text_layer:
            body = doc[number - 1].get_text().strip()
            if len(body) <= 200:
                continue
        else:
            body = await page_text(doc, number)
        if body.strip():
            yield body, number, ""


async def count_pieces(doc: "fitz.Document", by_genus: bool, numbers: list[int]) -> int:
    """Сколько кусков в книге. Для книг без текстового слоя — оценка по страницам."""
    if by_genus:
        pages = [doc[n - 1].get_text() for n in range(1, doc.page_count + 1)]
        return sum(1 for a in find_genus_articles(pages)
                   if a.page in numbers and article_has_care(a))
    return len(numbers)


async def ingest_book(
    source_id: str,
    *,
    book: str | None = None,
    by_genus: bool = False,
    pages: list[int] | None = None,
    limit: int = 0,
    start_at: int = 0,
    apply: bool = True,
    on_piece: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Разбирает книгу и пишет уход в базу. Возвращает сводку прогона.

    ``start_at`` пропускает первые куски: так прогон продолжается после
    перезапуска, не переразбирая уже сделанное. ``on_piece`` вызывается после
    каждого куска — активность отбивает этим heartbeat, а скрипт печатает строку.
    """
    meta = dict(HOUSEPLANT_BOOKS.get(source_id) or {})
    book = book or meta.get("object")
    if not book:
        raise ValueError(f"не знаю, какой файл разбирать для источника {source_id}")

    doc = open_book(book)
    numbers = pages or list(range(1, doc.page_count + 1))
    total = await count_pieces(doc, by_genus, numbers)

    if apply:
        await save_source(source_id, book)

    done = plants = care = problems = saved_care = saved_problems = 0
    async for body, page, genus in book_pieces(doc, by_genus, numbers):
        done += 1
        if done <= start_at:
            continue
        if limit and done > start_at + limit:
            break

        try:
            found = await extract_article(body, page=page, fallback_latin=genus)
        except Exception as error:      # один кусок не должен валить прогон
            if on_piece:
                await on_piece({"done": done, "total": total, "page": page,
                                "error": str(error)[:200]})
            continue

        for plant in found:
            plants += 1
            care += len(plant["care"])
            problems += len(plant["problems"])
            if apply:
                rows, problem_rows = await save_plant(source_id, plant)
                saved_care += rows
                saved_problems += problem_rows

        if on_piece:
            await on_piece({"done": done, "total": total, "page": page,
                            "plants": plants, "care": care, "problems": problems,
                            "saved_care": saved_care, "saved_problems": saved_problems})

    return {"source": source_id, "book": book, "pieces": done, "total": total,
            "plants": plants, "care": care, "problems": problems,
            "saved_care": saved_care, "saved_problems": saved_problems,
            "applied": apply}
