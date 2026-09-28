"""Названия рецептов по составу: постановка прогона в Temporal, итог, откат.

    # сухой прогон на 20 рецептах: только предложения и проверка, база не меняется
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/recipe_rename_run.py --limit 20
    # итог прогона: предложенные и отклонённые названия
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/recipe_rename_run.py --result recipe-rename-dry-20
    # все безликие индексируемые рецепты, с записью
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/recipe_rename_run.py --apply
    # вернуть прежние названия по журналу processing_log
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/recipe_rename_run.py --revert
"""

import argparse
import asyncio
import json
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="сколько рецептов (0 — все)")
    parser.add_argument("--apply", action="store_true", help="записывать новые названия")
    parser.add_argument("--force", action="store_true", help="и уже переименованные")
    parser.add_argument("--result", default="", help="итог прогона с этим id")
    parser.add_argument("--revert", action="store_true", help="вернуть прежние названия")
    args = parser.parse_args()

    if args.revert:
        from app.services.recipe_names import revert_recipe_rename
        print(await revert_recipe_rename())
        return 0

    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    if args.result:
        h = client.get_workflow_handle(args.result)
        d = await h.describe()
        print(f"{args.result}: {d.status.name if d.status else '?'}")
        if d.status and d.status.name == "COMPLETED":
            print(json.dumps(await h.result(), ensure_ascii=False, indent=1))
        return 0

    wid = "recipe-rename" + ("" if args.apply else "-dry") + (f"-{args.limit}" if args.limit else "")
    try:
        h = await client.start_workflow("RecipeRenameWorkflow", args=[args.apply, args.limit, args.force],
                                        id=wid, task_queue="dispatcher")
    except Exception as e:  # noqa: BLE001 — уже идущий прогон второй раз не запускаем
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {h.id}\nИтог: --result {h.id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
