"""Добор фотографий карточек из iNaturalist: постановка задачи в Temporal.

Проход ходит во внешний API по одному запросу в две секунды и занимает часы,
поэтому запускается только как воркфлоу в очереди диспетчера.

    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/photo_backfill_run.py --limit 15   # проба
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/photo_backfill_run.py              # все карточки

Воркфлоу запускается по имени, поэтому скрипту не нужен свежий образ backend.
Живость проверять по данным:
    SELECT count(*) FROM plants WHERE photo_url IS NOT NULL;
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="сколько карточек обработать через API (0 — все)")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    try:
        handle = await client.start_workflow(
            "PhotoBackfillWorkflow",
            args=[args.limit],
            id="photo-backfill" + (f"-{args.limit}" if args.limit else ""),
            task_queue="dispatcher",
        )
    except Exception as e:  # noqa: BLE001 — уже идущий прогон не запускаем второй раз
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {handle.id}")
    print("Смотреть за ходом: /api/ops.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
