"""Разбор пособия по ядовитым комнатным: постановка задачи в Temporal.

Книга распознаётся постранично и разбирается по статьям, то есть идёт часами.
Такие прогоны в проекте делает Temporal: задача, запущенная через
``docker compose exec``, живёт в контейнере backend и умирает вместе с
пересборкой или с оборванной ssh-сессией.

    docker compose exec -T backend python /app/scripts/houseplant_toxicity_run.py \
        --source morozova2019 --limit 3     # пробный разбор трёх статей
    docker compose exec -T backend python /app/scripts/houseplant_toxicity_run.py \
        --source morozova2019               # книга целиком

Ход прогона виден на странице ops вместе с остальными долгими задачами.
"""

import argparse
import asyncio
import sys

from app.services.houseplant_care import HOUSEPLANT_BOOKS


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=sorted(HOUSEPLANT_BOOKS))
    parser.add_argument("--book", help="объект в бакете; по умолчанию из описания книги")
    parser.add_argument("--limit", type=int, default=0,
                        help="сколько статей разобрать (0 — всю книгу)")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client
    from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
    from app.temporal.workflows import HouseplantToxicityWorkflow

    book = args.book or HOUSEPLANT_BOOKS[args.source]["object"]
    client = await get_temporal_client()
    handle = await client.start_workflow(
        HouseplantToxicityWorkflow.run,
        args=[args.source, book, args.limit],
        # Пробный разбор и полный — разные запуски, чтобы один не мешал другому.
        id=f"houseplant-toxicity-{args.source}" + (f"-{args.limit}" if args.limit else ""),
        task_queue=DISPATCHER_TASK_QUEUE,
    )
    print(f"Разбор поставлен: {handle.id}")
    print("Смотреть за ходом: /api/ops.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
