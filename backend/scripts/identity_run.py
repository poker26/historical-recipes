"""Чистка идентичности карточек: постановка шага в Temporal.

Шаги идут по одному, в этом порядке: dedup → shells → gbif → reid → resolve (разбор очереди просмотра правилами). У dedup и
shells сначала сухой прогон (без --apply): он только считает и показывает
выборку, результат читается из воркфлоу.

    docker compose exec -T -e PYTHONPATH=/app backend python - --step dedup < backend/scripts/identity_run.py
    docker compose exec -T -e PYTHONPATH=/app backend python - --step dedup --apply < backend/scripts/identity_run.py
    docker compose exec -T -e PYTHONPATH=/app backend python - --step shells --apply --limit 50 < backend/scripts/identity_run.py
    docker compose exec -T -e PYTHONPATH=/app backend python - --step gbif < backend/scripts/identity_run.py
    docker compose exec -T -e PYTHONPATH=/app backend python - --step reid --limit 20 < backend/scripts/identity_run.py

Воркфлоу запускается по имени, свежий образ backend не нужен. Журнал действий:
    SELECT step, action, count(*) FROM card_identity_audit GROUP BY 1, 2;
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["dedup", "shells", "gbif", "reid", "resolve"])
    parser.add_argument("--apply", action="store_true", help="для dedup и shells: применять, а не считать")
    parser.add_argument("--limit", type=int, default=0, help="сколько карточек обработать (0 — все)")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client

    wid = f"identity-{args.step}"
    if args.step in ("dedup", "shells", "resolve") and not args.apply:
        wid += "-dry"
    if args.limit:
        wid += f"-{args.limit}"
    client = await get_temporal_client()
    try:
        handle = await client.start_workflow(
            "IdentityCleanupWorkflow", args=[args.step, args.apply, args.limit],
            id=wid, task_queue="dispatcher")
    except Exception as e:  # noqa: BLE001 — уже идущий шаг не запускаем второй раз
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Шаг поставлен: {handle.id}")
    print("Смотреть за ходом: /api/ops; результат сухого прогона — describe/result воркфлоу.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
