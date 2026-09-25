"""Привязка фактов к страницам сканов: постановка задачи в Temporal.

Прогон идёт по всему корпусу и занимает часы, поэтому запускается только как
воркфлоу в очереди диспетчера: он переживает пересборку backend и обрыв ssh и
продолжается с последней законченной книги.

    docker compose exec -T backend python /app/scripts/page_anchor_run.py --limit 3   # проба на трёх книгах
    docker compose exec -T backend python /app/scripts/page_anchor_run.py             # весь корпус

Ход прогона виден на странице ops вместе с остальными долгими задачами.
Проверять живость по данным:
    SELECT anchor_method, count(*) FROM plant_medicinal_uses GROUP BY 1;
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="сколько книг обработать (0 — весь корпус)")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client
    from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
    from app.temporal.workflows import PageAnchorWorkflow

    client = await get_temporal_client()
    try:
        handle = await client.start_workflow(
            PageAnchorWorkflow.run,
            args=[args.limit],
            # Пробный и полный прогоны это разные запуски, чтобы один не мешал другому.
            id="page-anchor" + (f"-{args.limit}" if args.limit else ""),
            task_queue=DISPATCHER_TASK_QUEUE,
        )
    except Exception as e:  # noqa: BLE001 — уже идущий прогон не запускаем второй раз
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {handle.id}")
    print("Смотреть за ходом: /api/ops.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
