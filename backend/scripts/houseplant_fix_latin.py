"""Сверка латинских имён слоя комнатных с GBIF.

Распознавание книг портит латынь. В базу уже приехал «Stepttanotis» вместо
Stephanotis, и без сверки один род расползётся по нескольким написаниям, а
карточка покажет человеку половину того, что книги про него говорят.

Запуск на проде:

    cat backend/scripts/houseplant_fix_latin.py \\
      | docker compose exec -T backend python - --apply

Без ``--apply`` печатает, что бы он поменял, и ничего не трогает. Повторный
запуск безопасен: имена, которые GBIF уже подтвердил, остаются на месте.

Если после переименования у растения оказываются две строки с одной цитатой,
лишняя удаляется: это тот же голос той же книги, просто записанный под двумя
написаниями имени.
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database import async_session
from app.services.houseplant_care import resolve_latin


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="переименовать в базе")
    args = parser.parse_args()

    async with async_session() as db:
        names = [r[0] for r in (await db.execute(text(
            "SELECT DISTINCT taxon_latin FROM houseplant_care ORDER BY taxon_latin"
        ))).all()]

    print(f"Имён в слое: {len(names)}")

    changes: list[tuple[str, str]] = []
    unknown: list[str] = []
    verified: list[str] = []
    for name in names:
        accepted, ok = await resolve_latin(name)
        if not ok:
            unknown.append(name)
            continue
        verified.append(accepted)
        if accepted and accepted != name:
            changes.append((name, accepted))
            print(f"  {name} → {accepted}")

    print(f"\nGBIF подтвердил: {len(verified)}, поправил написание: {len(changes)}")
    if unknown:
        print(f"Не узнал ({len(unknown)}): {', '.join(unknown)}")
        print("Такие строки остаются в базе, но помечены непроверенными.")
    if not args.apply:
        print("Ничего не изменено: для записи нужен --apply")
        return 0

    renamed = merged = 0
    async with async_session() as db:
        for old, new in changes:
            # Строки, у которых после переименования появится близнец с той же
            # цитатой, переносить некуда: их место уже занято.
            await db.execute(text("""
                DELETE FROM houseplant_care a
                USING houseplant_care b
                WHERE a.taxon_latin = :old AND b.taxon_latin = :new
                  AND a.field = b.field
                  AND COALESCE(a.season, '') = COALESCE(b.season, '')
                  AND a.source_id = b.source_id
                  AND md5(a.quote) = md5(b.quote)
            """), {"old": old, "new": new})
            merged += 1

            result = await db.execute(text(
                "UPDATE houseplant_care SET taxon_latin = :new, latin_verified = true "
                "WHERE taxon_latin = :old"
            ), {"old": old, "new": new})
            renamed += result.rowcount or 0

            await db.execute(text(
                "UPDATE houseplant_problem SET taxon_latin = :new WHERE taxon_latin = :old"
            ), {"old": old, "new": new})
        await db.commit()

        # Имена, которые GBIF подтвердил без правки написания, тоже перестают
        # быть непроверенными.
        if verified:
            await db.execute(text(
                "UPDATE houseplant_care SET latin_verified = true "
                "WHERE taxon_latin = ANY(:names)"), {"names": verified})
            await db.commit()

    print(f"Переименовано строк ухода: {renamed}, имён затронуто: {merged}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
