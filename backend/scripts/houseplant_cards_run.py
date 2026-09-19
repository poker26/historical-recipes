"""Пересборка карточек комнатных растений: постановка задачи в Temporal.

Сборка идёт без вызовов модели, но тянет сотни фотографий из iNaturalist,
поэтому живёт в воркере, а не в ssh-сессии.

    docker compose exec -T backend python /app/scripts/houseplant_cards_run.py
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-facts", type=int, default=3,
                        help="минимум утверждений на карточку")
    parser.add_argument("--limit", type=int, default=0, help="сколько карточек собрать")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client
    from app.temporal.dispatcher_worker import DISPATCHER_TASK_QUEUE
    from app.temporal.workflows import HouseplantCardsWorkflow

    client = await get_temporal_client()
    handle = await client.start_workflow(
        HouseplantCardsWorkflow.run,
        args=[args.min_facts, args.limit],
        id="houseplant-cards",
        task_queue=DISPATCHER_TASK_QUEUE,
    )
    print(f"Пересборка карточек поставлена: {handle.id}")
    print("Смотреть за ходом: /api/ops.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
