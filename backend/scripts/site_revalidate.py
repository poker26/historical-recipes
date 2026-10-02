"""Сбросить кэш карточек на сайте вручную: после правки данных руками или прогона без отметок.

    docker compose exec -T -e PYTHONPATH=/app dispatcher python - --ids <uuid>,<uuid> < backend/scripts/site_revalidate.py
    docker compose exec -T -e PYTHONPATH=/app dispatcher python - --since "2026-10-03 10:00" < backend/scripts/site_revalidate.py

--since берёт карточки, у которых после этого момента менялся очерк или была запись в журнале
чистки идентичности (слияние, смена латыни). Нужен REVALIDATE_SECRET в окружении контейнера.
"""

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="", help="UUID карточек через запятую")
    parser.add_argument("--since", default="", help="момент времени, UTC: «2026-10-03 10:00»")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.database import async_session
    from app.services import site_cache

    ids = {x.strip() for x in args.ids.split(",") if x.strip()}
    if args.since:
        async with async_session() as db:
            rows = (await db.execute(text("""
                SELECT plant_id::text FROM plant_reader_monograph WHERE updated_at >= CAST(:s AS timestamptz)
                UNION SELECT plant_id::text FROM card_identity_audit WHERE at >= CAST(:s AS timestamptz)
                UNION SELECT target_id::text FROM card_identity_audit
                WHERE at >= CAST(:s AS timestamptz) AND target_id IS NOT NULL"""),
                {"s": args.since})).all()
        ids |= {r[0] for r in rows if r[0]}
    if not ids:
        print("нечего сбрасывать: нет --ids и ничего не менялось после --since")
        return 1
    done = await site_cache.revalidate_plants(sorted(ids))
    print(f"карточек к сбросу {len(ids)}, сайт принял {done}")
    return 0 if done else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
