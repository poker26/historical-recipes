"""Сборка карточек комнатных растений из слоя ухода.

Человек снял горшок, определитель назвал вид, а карточки нет — этой командой она
появляется. Сборка детерминированная: ни одного вызова модели, только фразы книг
со ссылками на страницы, поэтому запускать её можно сколько угодно раз.

    docker compose exec -T backend python /app/scripts/houseplant_build_cards.py --apply

Без ``--apply`` печатает, сколько карточек получилось бы, и ничего не пишет.
"""

import argparse
import asyncio
import sys

from app.database import async_session
from app.services.houseplant_cards import build_cards


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="сколько карточек собрать")
    parser.add_argument("--min-facts", type=int, default=3,
                        help="минимум утверждений на карточку (по умолчанию 3)")
    parser.add_argument("--apply", action="store_true", help="писать в базу")
    args = parser.parse_args()

    if not args.apply:
        async with async_session() as db:
            from sqlalchemy import text
            rows = (await db.execute(text("""
                SELECT count(*) FROM (
                    SELECT taxon_latin FROM houseplant_care
                    WHERE latin_verified AND NOT greenhouse
                    GROUP BY taxon_latin HAVING count(*) >= :n
                ) x
            """), {"n": args.min_facts})).scalar()
        print(f"карточек получилось бы: {rows}")
        print("Ничего не записано: для записи нужен --apply")
        return 0

    async with async_session() as db:
        out = await build_cards(db, limit=args.limit, min_facts=args.min_facts)
    print(f"растений в слое: {out['plants_in_layer']}")
    print(f"карточек собрано: {out['cards']} (новых {out['created']}, обновлено {out['updated']})")
    print(f"пропущено как слишком тонкие: {out['skipped_thin']}")
    print(f"с фотографией из iNaturalist: {out['with_photo']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
