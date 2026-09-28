"""Прогрев botanik.fun: постановка обхода карты сайта в Temporal и его итог.

Обход всех адресов идёт часами, поэтому запускается только как воркфлоу в очереди
диспетчера (``SiteWarmWorkflow``).

    # пробный обход 200 адресов: проверить, что всё отвечает, и увидеть время ответа
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/site_warm_run.py --limit 200
    # полный обход в темпе 2 запроса в секунду
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/site_warm_run.py
    # только карточки и рецепты
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/site_warm_run.py --only plants,recipes
    # итог последнего прогона: сколько адресов, коды ответа, самые медленные страницы
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/site_warm_run.py --result site-warm-200
"""

import argparse
import asyncio
import json


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="сколько адресов обойти (0 — все)")
    parser.add_argument("--rps", type=float, default=2.0, help="запросов в секунду")
    parser.add_argument("--only", default="", help="карты с такими префиксами имени, через запятую")
    parser.add_argument("--result", default="", help="показать итог прогона с этим id")
    args = parser.parse_args()

    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    if args.result:
        handle = client.get_workflow_handle(args.result)
        desc = await handle.describe()
        print(f"{args.result}: {desc.status.name if desc.status else '?'}")
        if desc.status and desc.status.name == "COMPLETED":
            print(json.dumps(await handle.result(), ensure_ascii=False, indent=1))
        return 0

    only = [x.strip() for x in args.only.split(",") if x.strip()] or None
    wid = "site-warm" + (f"-{args.limit}" if args.limit else "") + (f"-{'-'.join(only)}" if only else "")
    try:
        handle = await client.start_workflow(
            "SiteWarmWorkflow", args=[args.limit, args.rps, only], id=wid, task_queue="dispatcher")
    except Exception as e:  # noqa: BLE001 — уже идущий прогон второй раз не запускаем
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {handle.id}")
    print(f"Итог: --result {handle.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
