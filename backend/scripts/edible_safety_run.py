"""Шкала съедобности (RFC-edible-safety): постановка прогона в Temporal и сводка.

Прогон ставит уровень 0–4 карточкам видов и родов, у которых его нет. Карточки без
признаков ядовитости получают уровень сразу, остальные решает модель. Перед
проходом сбрасываются устаревшие быстрые уровни, в конце is_toxic выравнивается по
уровню. Запускается только как воркфлоу в очереди диспетчера.

    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/edible_safety_run.py
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/edible_safety_run.py --status
"""

import argparse
import asyncio
import sys


async def status() -> None:
    from sqlalchemy import text
    from app.database import async_session

    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT safety_level, count(*) AS n, count(*) FILTER (WHERE photo_url IS NOT NULL) AS ph "
            "FROM plants WHERE kingdom IN ('растение', 'гриб') AND rank IN ('species', 'genus') "
            "GROUP BY 1 ORDER BY 1 NULLS FIRST"))).all()
        for r in rows:
            print(f"уровень {r.safety_level}: карточек {r.n}, из них с фото {r.ph}")
        bad = (await db.execute(text(
            "SELECT count(*) FROM plants WHERE safety_level IS NOT NULL "
            "AND is_toxic IS DISTINCT FROM (safety_level >= 3)"))).scalar()
        print(f"is_toxic расходится с уровнем: {bad}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="только сводка")
    args = parser.parse_args()
    if args.status:
        await status()
        return 0

    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    try:
        handle = await client.start_workflow(
            "EdibleSafetyWorkflow", id="edible-safety", task_queue="dispatcher")
    except Exception as e:  # noqa: BLE001 — уже идущий прогон второй раз не запускаем
        print(f"Не поставлен: {type(e).__name__}: {str(e)[:160]}")
        return 1
    print(f"Прогон поставлен: {handle.id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
