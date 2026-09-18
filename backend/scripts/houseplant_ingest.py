"""Заливка ухода из книги: постановка в Temporal и короткая проверка на месте.

Долгие прогоны в проекте делает Temporal, потому что задача, запущенная через
``docker compose exec``, живёт в контейнере backend и умирает от его
пересборки. Поэтому по умолчанию скрипт не разбирает книгу сам, а ставит
воркфлоу и сразу отдаёт консоль:

    docker compose exec -T backend python /app/scripts/houseplant_ingest.py \\
        --source saakov1985 --by-genus --start

Наблюдать за ним можно на странице ops вместе с остальными долгими прогонами.

Ключ ``--here`` разбирает книгу прямо в этом процессе. Он годится для короткой
проверки на нескольких страницах («что вообще извлечётся с 54-й»), но не для
книги целиком.

Как режется книга. У Саакова есть родовые заголовки, и куски берутся по ним:
уход стоит в конце статьи, и разрез посреди страницы оставил бы полив в одном
куске, а землю в другом. У остальных книг заголовков нет, и кусок — это
страница.

Повторный запуск безопасен: один и тот же кусок скана от одного источника не
ложится дважды, это отсекается ограничением в базе. Разные утверждения по
одному полю при этом живут рядом: хойя у Саакова размножается тремя способами,
и схлопывать их в одну строку значит терять книгу.
"""

import argparse
import asyncio
import sys

from app.services.houseplant_care import HOUSEPLANT_BOOKS
from app.services.houseplant_ingest import ingest_book


def _parse_pages(spec: str) -> list[int]:
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            first, last = chunk.split("-", 1)
            pages.extend(range(int(first), int(last) + 1))
        elif chunk:
            pages.append(int(chunk))
    return pages


async def _start_workflow(source: str, by_genus: bool, pages: list[int] | None,
                          limit: int) -> int:
    """Ставит заливку в Temporal и печатает, где за ней смотреть."""
    from app.temporal.client import get_temporal_client
    from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
    from app.temporal.workflows import HouseplantIngestWorkflow

    client = await get_temporal_client()
    handle = await client.start_workflow(
        HouseplantIngestWorkflow.run,
        args=[source, by_genus, pages, limit],
        id=f"houseplant-{source}",          # одна книга — одна заливка
        # Очередь диспетчера: там живут долгие чистящие прогоны и там же
        # зарегистрирована наша активность.
        task_queue=DISPATCHER_TASK_QUEUE,
    )
    print(f"Заливка поставлена: {handle.id}")
    print("Смотреть за ходом: /api/ops.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=sorted(HOUSEPLANT_BOOKS))
    parser.add_argument("--book", help="объект в бакете; по умолчанию берётся из описания книги")
    parser.add_argument("--pages", help="страницы, например 223-225; без них берётся вся книга")
    parser.add_argument("--by-genus", action="store_true",
                        help="резать по родовым заголовкам (Сааков)")
    parser.add_argument("--limit", type=int, default=0, help="сколько кусков разобрать")
    parser.add_argument("--start", action="store_true",
                        help="поставить заливку в Temporal (так и надо для книги целиком)")
    parser.add_argument("--here", action="store_true",
                        help="разобрать прямо сейчас, в этом процессе (только для проверки)")
    parser.add_argument("--apply", action="store_true", help="писать в базу при --here")
    args = parser.parse_args()

    pages = _parse_pages(args.pages) if args.pages else None

    if args.start:
        return await _start_workflow(args.source, args.by_genus, pages, args.limit)

    if not args.here:
        print("Ничего не запущено. Книга целиком ставится в Temporal ключом --start; "
              "для короткой проверки есть --here [--apply].")
        return 1

    async def report(progress: dict) -> None:
        if progress.get("error"):
            print(f"  [{progress['done']}/{progress['total']}] стр. {progress['page']}: "
                  f"разбор не удался — {progress['error']}", flush=True)
            return
        print(f"  [{progress['done']}/{progress['total']}] стр. {progress['page']}: "
              f"растений {progress['plants']}, уход {progress['care']}, "
              f"болезни {progress['problems']}", flush=True)

    out = await ingest_book(args.source, book=args.book, by_genus=args.by_genus,
                            pages=pages, limit=args.limit, apply=args.apply,
                            on_piece=report)
    print(f"\nРастений: {out['plants']}, утверждений об уходе: {out['care']}, "
          f"записей о болезнях: {out['problems']}")
    if args.apply:
        print(f"Записано: уход {out['saved_care']}, болезни {out['saved_problems']} "
              f"(повторы отсечены ограничением базы)")
    else:
        print("Ничего не записано: для записи нужен --apply")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
