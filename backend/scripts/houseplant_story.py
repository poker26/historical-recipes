"""Разбор книг рассказа о растении: постановка в Temporal.

Книга целиком разбирается только воркфлоу (``--start``): разбор идёт часами и
должен переживать пересборку backend. Проба — тот же воркфлоу с ``--limit``.

    docker compose exec -T backend python /app/scripts/houseplant_story.py \\
        --source zhr1982t6 --start [--pages 470-482] [--limit 5]

``--all`` ставит все книги слоя рассказа, каждую своим воркфлоу.
``--cards`` вписывает разобранный рассказ в карточки.
"""

import argparse
import asyncio
import sys

from app.services.houseplant_care import HOUSEPLANT_BOOKS

STORY_SOURCES = sorted(k for k, v in HOUSEPLANT_BOOKS.items() if v.get("audience") == "story")


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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=STORY_SOURCES)
    parser.add_argument("--all", action="store_true", help="все книги слоя рассказа")
    parser.add_argument("--pages", help="страницы PDF, например 470-482")
    parser.add_argument("--limit", type=int, default=0, help="сколько страниц разобрать")
    parser.add_argument("--start", action="store_true", help="поставить в Temporal")
    parser.add_argument("--cards", action="store_true",
                        help="вписать уже разобранный рассказ в карточки (без разбора книг)")
    args = parser.parse_args()

    if args.cards:
        from app.temporal.client import get_temporal_client
        from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
        from app.temporal.workflows import HouseplantStoryCardsWorkflow

        client = await get_temporal_client()
        handle = await client.start_workflow(
            HouseplantStoryCardsWorkflow.run, id="houseplant-story-cards",
            task_queue=DISPATCHER_TASK_QUEUE)
        print(f"Поставлено: {handle.id}")
        return 0

    sources = STORY_SOURCES if args.all else [args.source] if args.source else []
    if not args.start or not sources:
        print("Ничего не запущено: нужен --start и --source или --all.")
        return 1

    from app.temporal.client import get_temporal_client
    from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
    from app.temporal.workflows import HouseplantStoryWorkflow

    pages = _parse_pages(args.pages) if args.pages else None
    client = await get_temporal_client()
    for source in sources:
        handle = await client.start_workflow(
            HouseplantStoryWorkflow.run,
            args=[source, pages, args.limit],
            id=f"houseplant-story-{source}",
            task_queue=DISPATCHER_TASK_QUEUE,
        )
        print(f"Поставлено: {handle.id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
