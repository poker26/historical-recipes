"""Публичная библиотека для сайта botanik.fun (RFC-botanik-site §5.5, §8, §10).

Только чтение. Три класса доступа к книге (§8.1):

* ``open``  — срок охраны истёк (в первой версии: известен год издания и он не позже
  ``OPEN_UNTIL_YEAR``, либо у книги тег ``open``): страницы и текст открыты целиком;
* ``cited`` — год неизвестен или книга охраняется: отдаём номер страницы, библиографию
  и только те фрагменты, которые уже опубликованы как факты корпуса (цитаты,
  рецепты), без картинки страницы и без её полного текста;
* ``closed`` — тег ``closed``: только карточка книги.

Классы это рабочее допущение до проверки юристом (RFC §13.1); порог года и теги
меняются без миграций. Картинки страниц берутся из MinIO (сканы) или рисуются из
исходного PDF (страницы с текстовым слоем), производные кэшируются в MinIO под
``books/{id}/derived/``.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import minio as minio_svc

logger = logging.getLogger(__name__)
router = APIRouter()

OPEN_UNTIL_YEAR = int(os.environ.get("LIBRARY_OPEN_UNTIL_YEAR", "1917"))
PDF_CACHE_DIR = os.environ.get("LIBRARY_PDF_CACHE", "/tmp/hr-books")
SIZES = {"thumb": 320, "medium": 1100, "full": 1800}   # ширина картинки, px
PUBLIC_STATUSES = ("indexed",)

BOOK_COLS = """
    b.id, b.title, b.author, b.year, b.domain, b.language, b.source_format, b.pdf_type, b.status, b.tags,
    (SELECT count(*) FROM book_pages p WHERE p.book_id = b.id) AS pages,
    (SELECT count(*) FROM book_pages p WHERE p.book_id = b.id AND p.image_path IS NOT NULL) AS scan_pages,
    (SELECT count(*) FROM book_pages p WHERE p.book_id = b.id AND p.raw_text IS NOT NULL AND length(p.raw_text) > 40) AS text_pages,
    (SELECT min(p.page_number) FROM book_pages p WHERE p.book_id = b.id AND p.image_path IS NOT NULL) AS first_scan_page,
    (SELECT count(DISTINCT m.plant_id) FROM plant_book_mentions m WHERE m.book_id = b.id) AS plants,
    (SELECT count(*) FROM recipes r WHERE r.book_id = b.id) AS recipes,
    (SELECT count(*) FROM recipes r WHERE r.book_id = b.id AND r.home_doable) AS home_recipes,
    (SELECT count(*) FROM plant_medicinal_uses u WHERE u.source_book_id = b.id) AS uses
"""


def access_class(year: int | None, tags: list[str] | None) -> str:
    t = set(tags or [])
    if "closed" in t:
        return "closed"
    if "open" in t:
        return "open"
    if year is not None and year <= OPEN_UNTIL_YEAR:
        return "open"
    return "cited"


def _book_dict(r: Any) -> dict:
    d = dict(r._mapping)
    d["id"] = str(d["id"])
    d["access"] = access_class(d.get("year"), d.get("tags"))
    d["has_cover"] = bool(d.get("first_scan_page")) or (d.get("source_format") in ("pdf", "djvu"))
    d.pop("tags", None)
    return d


@router.get("/stats")
async def library_stats(db: AsyncSession = Depends(get_db)):
    """Цифры для шапки библиотеки: книги, сканы, годы, страницы."""
    r = (await db.execute(text("""
        SELECT count(*) AS books,
               count(*) FILTER (WHERE year IS NOT NULL) AS with_year,
               count(*) FILTER (WHERE year IS NOT NULL AND year <= :open_year) AS open_books,
               min(year) AS year_min, max(year) AS year_max,
               (SELECT count(*) FROM book_pages) AS pages,
               (SELECT count(*) FROM book_pages WHERE image_path IS NOT NULL) AS scan_pages,
               (SELECT count(DISTINCT book_id) FROM book_pages WHERE image_path IS NOT NULL) AS books_with_scans
        FROM books WHERE status = ANY(:st)"""), {"open_year": OPEN_UNTIL_YEAR, "st": list(PUBLIC_STATUSES)})).first()
    domains = (await db.execute(text(
        "SELECT domain, count(*) FROM books WHERE status = ANY(:st) GROUP BY 1 ORDER BY 2 DESC"), {"st": list(PUBLIC_STATUSES)})).all()
    return {**dict(r._mapping), "domains": [{"domain": d, "count": n} for d, n in domains]}


@router.get("/books")
async def list_books(
    q: str | None = Query(None, description="подстрока в названии или авторе"),
    domain: str | None = Query(None, description="herbalism | recipes | reference | mycology | aromatherapy"),
    access: str | None = Query(None, description="open | cited"),
    scans: bool | None = Query(None, description="только книги со сканами страниц"),
    era: str | None = Query(None, description="pre1917 | soviet | modern | unknown"),
    sort: str = Query("year", pattern="^(year|title|plants|recipes|pages)$"),
    limit: int = Query(60, ge=1, le=400),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Полка библиотеки: книги с тем, что из них извлечено."""
    where = ["b.status = ANY(:st)"]
    params: dict[str, Any] = {"st": list(PUBLIC_STATUSES)}
    if q:
        where.append("(b.title ILIKE :q OR b.author ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    if domain:
        where.append("b.domain = :domain")
        params["domain"] = domain
    if era == "pre1917":
        where.append("b.year IS NOT NULL AND b.year <= 1917")
    elif era == "soviet":
        where.append("b.year BETWEEN 1918 AND 1991")
    elif era == "modern":
        where.append("b.year > 1991")
    elif era == "unknown":
        where.append("b.year IS NULL")
    if scans:
        where.append("EXISTS (SELECT 1 FROM book_pages p WHERE p.book_id = b.id AND p.image_path IS NOT NULL)")
    if access == "open":
        where.append(f"((b.year IS NOT NULL AND b.year <= {OPEN_UNTIL_YEAR}) OR 'open' = ANY(COALESCE(b.tags, ARRAY[]::varchar[])))")
    elif access == "cited":
        where.append(f"NOT ((b.year IS NOT NULL AND b.year <= {OPEN_UNTIL_YEAR}) OR 'open' = ANY(COALESCE(b.tags, ARRAY[]::varchar[])))")
    order = {
        "year": "b.year NULLS LAST, b.title",
        "title": "b.title",
        "plants": "plants DESC, b.title",
        "recipes": "recipes DESC, b.title",
        "pages": "pages DESC, b.title",
    }[sort]
    sql = f"SELECT {BOOK_COLS} FROM books b WHERE {' AND '.join(where)} ORDER BY {order} LIMIT :limit OFFSET :offset"
    rows = (await db.execute(text(sql), {**params, "limit": limit, "offset": offset})).all()
    total = (await db.execute(text(f"SELECT count(*) FROM books b WHERE {' AND '.join(where)}"), params)).scalar()
    return {"total": total, "items": [_book_dict(r) for r in rows]}


async def _book(db: AsyncSession, book_id: uuid.UUID) -> dict:
    r = (await db.execute(text(f"SELECT {BOOK_COLS}, b.file_path FROM books b WHERE b.id = :id"), {"id": book_id})).first()
    if r is None or r.status not in PUBLIC_STATUSES:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    d = _book_dict(r)
    d["file_path"] = r.file_path
    return d


@router.get("/books/{book_id}")
async def get_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Страница книги: описание, оглавление, самые упоминаемые растения, рецепты."""
    b = await _book(db, book_id)
    b.pop("file_path", None)
    top_plants = (await db.execute(text("""
        SELECT p.id, p.name, p.name_latin, p.name_modern, p.photo_url, p.kingdom, count(*) AS mentions,
               (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id = p.id AND u.source_book_id = :b) AS uses
        FROM plant_book_mentions m JOIN plants p ON p.id = m.plant_id
        WHERE m.book_id = :b AND p.kingdom IN ('растение', 'гриб')
        GROUP BY p.id ORDER BY uses DESC, mentions DESC, p.name LIMIT 36"""), {"b": book_id})).all()
    toc = (await db.execute(text("""
        SELECT title, section_type, start_line FROM book_sections
        WHERE book_id = :b AND title IS NOT NULL AND length(title) BETWEEN 3 AND 140
          AND section_type IN ('chapter_header', 'toc', 'appendix', 'introduction')
        ORDER BY start_line NULLS LAST LIMIT 400"""), {"b": book_id})).all()
    seen: set[str] = set()
    toc_items = []
    for t in toc:
        key = " ".join((t.title or "").lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        toc_items.append({"title": " ".join(t.title.split()), "type": t.section_type})
        if len(toc_items) >= 80:
            break
    recipes = (await db.execute(text("""
        SELECT id, name, category, recipe_kind, home_doable, source_page FROM recipes
        WHERE book_id = :b AND name IS NOT NULL
        ORDER BY home_doable DESC, procedure_score DESC NULLS LAST, name LIMIT 24"""), {"b": book_id})).all()
    kinds = (await db.execute(text(
        "SELECT recipe_kind, count(*) FROM recipes WHERE book_id = :b GROUP BY 1 ORDER BY 2 DESC"), {"b": book_id})).all()
    anchored = (await db.execute(text("""
        SELECT (SELECT count(*) FROM plant_medicinal_uses WHERE source_book_id = :b AND source_page IS NOT NULL)
             + (SELECT count(*) FROM recipes WHERE book_id = :b AND source_page IS NOT NULL) AS n"""), {"b": book_id})).scalar()
    pages_with_facts = (await db.execute(text("""
        SELECT source_page AS n, count(*) AS c FROM (
            SELECT source_page FROM plant_medicinal_uses WHERE source_book_id = :b AND source_page IS NOT NULL
            UNION ALL SELECT source_page FROM recipes WHERE book_id = :b AND source_page IS NOT NULL
            UNION ALL SELECT page_number FROM plant_book_mentions WHERE book_id = :b AND page_number IS NOT NULL
        ) x GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12"""), {"b": book_id})).all()
    return {
        **b,
        # `recipes` ниже — список первых рецептов; общее число остаётся здесь.
        "recipes_total": b.get("recipes"),
        "top_plants": [{**dict(r._mapping), "id": str(r.id)} for r in top_plants],
        "toc": toc_items,
        "recipes": [{**dict(r._mapping), "id": str(r.id)} for r in recipes],
        "recipe_kinds": [{"kind": k, "count": n} for k, n in kinds],
        "anchored_facts": anchored,
        "busiest_pages": [{"page": n, "facts": c} for n, c in pages_with_facts],
        "citation": _citation(b),
    }


def _citation(b: dict, page: int | None = None) -> str:
    parts = []
    if b.get("author"):
        parts.append(b["author"].rstrip("."))
    parts.append(b["title"])
    s = ". ".join(parts)
    if b.get("year"):
        s += f", {b['year']}"
    if page:
        s += f". С. {page}"
    return s + "."


@router.get("/books/{book_id}/pages/{page_number}")
async def get_page(book_id: uuid.UUID, page_number: int, db: AsyncSession = Depends(get_db)):
    """Страница источника: текст (для открытых книг), соседи, факты и растения на ней."""
    b = await _book(db, book_id)
    b.pop("file_path", None)
    page = (await db.execute(text("""
        SELECT page_number, image_path IS NOT NULL AS has_scan, raw_text, ocr_confidence
        FROM book_pages WHERE book_id = :b AND page_number = :n"""), {"b": book_id, "n": page_number})).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Страницы нет")
    nav = (await db.execute(text("""
        SELECT (SELECT max(page_number) FROM book_pages WHERE book_id = :b AND page_number < :n) AS prev,
               (SELECT min(page_number) FROM book_pages WHERE book_id = :b AND page_number > :n) AS next,
               (SELECT min(page_number) FROM book_pages WHERE book_id = :b) AS first,
               (SELECT max(page_number) FROM book_pages WHERE book_id = :b) AS last"""), {"b": book_id, "n": page_number})).first()

    facts: list[dict] = []
    p = {"b": book_id, "n": page_number}
    q_plant = """SELECT {kind} AS kind, f.id, f.plant_id, pl.name AS plant_name, pl.name_latin AS plant_latin, pl.photo_url,
                        f.original_text, f.anchor_score, {extra}
                 FROM {table} f JOIN plants pl ON pl.id = f.plant_id
                 WHERE f.source_book_id = :b AND f.source_page = :n ORDER BY f.anchor_score DESC NULLS LAST LIMIT 60"""
    for kind, table, extra in (
        ("'use'", "plant_medicinal_uses", "f.canon_action AS label, f.part"),
        ("'culinary'", "plant_culinary_uses", "f.use AS label, f.part"),
        ("'harvest'", "plant_harvests", "f.season AS label, f.part"),
        ("'habitat'", "plant_habitats", "f.region AS label, f.biotope AS part"),
        ("'toxicity'", "plant_toxicities", "f.severity AS label, f.toxic_parts AS part"),
    ):
        rows = (await db.execute(text(q_plant.format(kind=kind, table=table, extra=extra)), p)).all()
        facts.extend({**dict(r._mapping), "id": str(r.id), "plant_id": str(r.plant_id)} for r in rows)
    rows = (await db.execute(text("""
        SELECT 'mention' AS kind, m.id, m.plant_id, pl.name AS plant_name, pl.name_latin AS plant_latin, pl.photo_url,
               m.original_text, m.anchor_score, m.original_name AS label, NULL AS part
        FROM plant_book_mentions m JOIN plants pl ON pl.id = m.plant_id
        WHERE m.book_id = :b AND m.page_number = :n ORDER BY m.anchor_score DESC NULLS LAST LIMIT 60"""), p)).all()
    facts.extend({**dict(r._mapping), "id": str(r.id), "plant_id": str(r.plant_id)} for r in rows)
    recipes = (await db.execute(text("""
        SELECT id, name, category, recipe_kind, home_doable, original_text, anchor_score FROM recipes
        WHERE book_id = :b AND source_page = :n ORDER BY anchor_score DESC NULLS LAST LIMIT 40"""), p)).all()

    plants: dict[str, dict] = {}
    for f in facts:
        pl = plants.setdefault(f["plant_id"], {"id": f["plant_id"], "name": f["plant_name"], "name_latin": f["plant_latin"],
                                               "photo_url": f["photo_url"], "facts": 0})
        pl["facts"] += 1
    open_book = b["access"] == "open"
    return {
        "book": {k: b[k] for k in ("id", "title", "author", "year", "domain", "language", "access", "pages", "scan_pages")},
        "page": {
            "number": page.page_number,
            "has_scan": bool(page.has_scan),
            "has_image": open_book and (bool(page.has_scan) or b.get("source_format") in ("pdf", "djvu")),
            "text": page.raw_text if open_book else None,
            "text_len": len(page.raw_text or ""),
            "ocr_confidence": page.ocr_confidence,
        },
        "nav": dict(nav._mapping),
        "facts": facts,
        "recipes": [{**dict(r._mapping), "id": str(r.id)} for r in recipes],
        "plants": sorted(plants.values(), key=lambda x: -x["facts"]),
        "citation": _citation(b, page_number),
    }


# ---------------------------------------------------------------- картинки страниц

def _derived_path(book_id: uuid.UUID, page_number: int, size: str) -> str:
    return f"books/{book_id}/derived/{page_number:04d}_{size}.jpg"


def _render_from_png(png: bytes, width: int) -> bytes:
    """Скан → JPEG заданной ширины. PyMuPDF открывает PNG как документ и умеет
    масштабировать при отрисовке, поэтому отдельная библиотека картинок не нужна."""
    import fitz  # PyMuPDF, есть в образе (им же режут PDF на страницы)
    doc = fitz.open(stream=png, filetype="png")
    page = doc[0]
    scale = min(1.0, width / max(page.rect.width, 1))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    out = pix.tobytes("jpeg", jpg_quality=82)
    doc.close()
    return out


def _local_pdf(book_id: uuid.UUID, file_path: str) -> str:
    """Исходный PDF книги на локальном диске контейнера (скачивается один раз)."""
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    local = os.path.join(PDF_CACHE_DIR, f"{book_id}.pdf")
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        data = minio_svc.download_file(file_path)
        tmp = local + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, local)
    return local


def _render_from_pdf(book_id: uuid.UUID, file_path: str, page_number: int, width: int) -> bytes | None:
    import fitz
    local = _local_pdf(book_id, file_path)
    doc = fitz.open(local)
    try:
        if page_number < 1 or page_number > doc.page_count:
            return None
        page = doc[page_number - 1]
        scale = width / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=82)
    finally:
        doc.close()


def _page_image_sync(book_id: uuid.UUID, file_path: str | None, page_number: int, image_path: str | None, size: str) -> bytes | None:
    """Готовая производная из MinIO или отрисовка с кэшированием."""
    derived = _derived_path(book_id, page_number, size)
    if minio_svc.file_exists(derived):
        return minio_svc.download_file(derived)
    width = SIZES[size]
    data: bytes | None = None
    if image_path:
        try:
            data = _render_from_png(minio_svc.download_file(image_path), width)
        except Exception as e:  # noqa: BLE001 — битый PNG не должен валить страницу
            logger.warning("library: png render failed %s p%s: %s", book_id, page_number, e)
    if data is None and file_path and file_path.lower().endswith(".pdf"):
        try:
            data = _render_from_pdf(book_id, file_path, page_number, width)
        except Exception as e:  # noqa: BLE001
            logger.warning("library: pdf render failed %s p%s: %s", book_id, page_number, e)
    if data:
        try:
            minio_svc.upload_file(data, derived, content_type="image/jpeg")
        except Exception as e:  # noqa: BLE001 — кэш необязателен
            logger.warning("library: derived cache write failed %s: %s", derived, e)
    return data


@router.get("/books/{book_id}/pages/{page_number}/image")
async def get_page_image(book_id: uuid.UUID, page_number: int,
                         size: str = Query("medium", pattern="^(thumb|medium|full)$"),
                         db: AsyncSession = Depends(get_db)):
    """Картинка страницы. Только для открытых книг (§8.1); остальным 403."""
    b = await _book(db, book_id)
    if b["access"] != "open":
        raise HTTPException(status_code=403, detail="Страницы этой книги показываем только фрагментами")
    page = (await db.execute(text(
        "SELECT image_path FROM book_pages WHERE book_id = :b AND page_number = :n"), {"b": book_id, "n": page_number})).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Страницы нет")
    data = await run_in_threadpool(_page_image_sync, book_id, b.get("file_path"), page_number, page.image_path, size)
    if not data:
        raise HTTPException(status_code=404, detail="Картинки страницы нет")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"})


@router.get("/books/{book_id}/cover")
async def get_cover(book_id: uuid.UUID, size: str = Query("thumb", pattern="^(thumb|medium)$"),
                    db: AsyncSession = Depends(get_db)):
    """Обложка: первая страница скана или первая страница PDF. Титул это библиографическая
    информация, поэтому миниатюра отдаётся для всех классов доступа, кроме закрытых."""
    b = await _book(db, book_id)
    if b["access"] == "closed":
        raise HTTPException(status_code=403, detail="Закрытая книга")
    page = (await db.execute(text("""
        SELECT page_number, image_path FROM book_pages WHERE book_id = :b
        ORDER BY (image_path IS NULL), page_number LIMIT 1"""), {"b": book_id})).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Страниц нет")
    data = await run_in_threadpool(_page_image_sync, book_id, b.get("file_path"), page.page_number, page.image_path, size)
    if not data:
        raise HTTPException(status_code=404, detail="Обложки нет")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800, stale-while-revalidate=2592000"})


@router.get("/books/{book_id}/search")
async def search_in_book(book_id: uuid.UUID, q: str = Query(..., min_length=3, max_length=80),
                         limit: int = Query(20, ge=1, le=50), db: AsyncSession = Depends(get_db)):
    """Поиск по тексту страниц одной книги. Для открытых книг отдаём фрагмент вокруг
    совпадения, для остальных только номера страниц (сам текст не раскрываем)."""
    b = await _book(db, book_id)
    rows = (await db.execute(text("""
        SELECT page_number, raw_text FROM book_pages
        WHERE book_id = :b AND raw_text ILIKE :q ORDER BY page_number LIMIT :lim"""),
        {"b": book_id, "q": f"%{q.strip()}%", "lim": limit})).all()
    out = []
    needle = q.strip().lower()
    for r in rows:
        item: dict[str, Any] = {"page": r.page_number}
        if b["access"] == "open" and r.raw_text:
            i = r.raw_text.lower().find(needle)
            start = max(0, i - 120)
            item["snippet"] = " ".join(r.raw_text[start:i + len(needle) + 160].split())
        out.append(item)
    return {"q": q, "book_id": str(book_id), "access": b["access"], "hits": out}
