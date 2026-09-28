"""Год издания и автор книг: постановка прогона в Temporal и сводка по итогам.

Прогон спрашивает модель по каждой книге и идёт часами, поэтому запускается
только как воркфлоу в очереди диспетчера (``BookMetaWorkflow``).

    # пробный сухой прогон на 12 книгах: только находки, книги не меняются
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/book_meta_run.py --limit 12
    # все книги, надёжное записывается сразу, остальное уходит на ревью
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/book_meta_run.py --apply
    # сводка: сколько книг с годом и автором, что ждёт ревью
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/book_meta_run.py --status
    # откат всего, что прогон записал сам (по журналу processing_log, шаг book_meta)
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/book_meta_run.py --revert

Воркфлоу запускается по имени, поэтому скрипту не нужен свежий образ backend.
Ревью: админка, раздел «Качество», проверка ``book.meta``.
"""

import argparse
import asyncio
import sys


async def status() -> None:
    from sqlalchemy import text
    from app.database import async_session

    async with async_session() as db:
        b = (await db.execute(text(
            "SELECT count(*) AS books, count(*) FILTER (WHERE year IS NOT NULL) AS with_year, "
            "count(*) FILTER (WHERE coalesce(trim(author), '') <> '') AS with_author, "
            "count(*) FILTER (WHERE year IS NOT NULL AND year <= 1917) AS pre1918 "
            "FROM books"))).first()
        print(f"книг {b.books}: с годом {b.with_year}, с автором {b.with_author}, с годом до 1918 {b.pre1918}")
        rows = (await db.execute(text(
            "SELECT status, severity, count(*) AS n FROM data_quality_findings "
            "WHERE check_id = 'book.meta' GROUP BY 1, 2 ORDER BY 1, 2"))).all()
        for r in rows:
            print(f"  находки {r.status} {r.severity}: {r.n}")
        logged = (await db.execute(text(
            "SELECT details->>'by' AS by, status, count(*) AS n FROM processing_log "
            "WHERE step = 'book_meta' GROUP BY 1, 2 ORDER BY 1, 2"))).all()
        for r in logged:
            print(f"  журнал {r.by} {r.status}: {r.n}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="сколько книг разобрать (0 — все)")
    parser.add_argument("--apply", action="store_true", help="записывать надёжное сразу")
    parser.add_argument("--force", action="store_true", help="разобрать заново и книги с находкой")
    parser.add_argument("--status", action="store_true", help="только сводка")
    parser.add_argument("--revert", action="store_true", help="откатить записанное прогоном")
    args = parser.parse_args()

    if args.status:
        await status()
        return 0
    if args.revert:
        from app.services.book_meta import revert_book_meta
        print(await revert_book_meta("book-meta"))
        return 0

    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    wid = "book-meta" + ("" if args.apply else "-dry") + (f"-{args.limit}" if args.limit else "")
    try:
        handle = await client.start_workflow(
            "BookMetaWorkflow",
            args=[args.apply, args.limit, args.force],
            id=wid,
            task_queue="dispatcher",
        )
    except Exception as e:  # noqa: BLE001 — уже идущий прогон второй раз не запускаем
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {handle.id}")
    print("Ход: --status; находки в админке, проверка book.meta.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
