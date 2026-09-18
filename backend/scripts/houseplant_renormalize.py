"""Пересчёт нормализованных значений уже записанного ухода.

Правила нормализации уточняются по живым книгам: Сааков пишет градусы без
буквы («20—22°»), свет описывает словами «светолюбивые» и «в осветлённых
местах», а в базу успели попасть заголовки рубрик вроде «Размножение:».
Заново гонять модель ради этого не нужно: фраза книги уже лежит в строке,
пересчитать по ней значение можно без единого вызова.

Запуск на проде:

    docker compose exec -T backend python /app/houseplant_renormalize.py --apply

Без ``--apply`` печатает, сколько строк изменилось бы, и ничего не трогает.
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import text

from app.database import async_session
from app.services.houseplant_care import (
    normalize_light,
    normalize_temperature,
    normalize_watering,
)


def value_for(field_name: str, value_text: str) -> dict:
    """Нынешнее нормализованное значение для фразы книги."""
    if field_name == "water":
        rules = normalize_watering(value_text)
        if not rules:
            return {}
        rule = dict(rules[0])
        rule.pop("season", None)
        return rule
    if field_name == "temperature":
        value = normalize_temperature(value_text)
        value.pop("season", None)
        return value
    if field_name == "light":
        return normalize_light(value_text)
    return {}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT id, field, value_text, value::text AS value
            FROM houseplant_care
            WHERE field IN ('water', 'temperature', 'light')
        """))).all()

    changed: list[tuple[str, dict]] = []
    for row in rows:
        fresh = value_for(row.field, row.value_text)
        if json.dumps(fresh, ensure_ascii=False, sort_keys=True) != json.dumps(
                json.loads(row.value), ensure_ascii=False, sort_keys=True):
            changed.append((row.id, fresh))

    filled = sum(1 for _, v in changed if v)
    print(f"строк с числовыми полями: {len(rows)}, изменится: {len(changed)}, "
          f"из них получат значение: {filled}")

    # Заголовки рубрик советом не являются: «Размножение:» ничего не говорит.
    async with async_session() as db:
        headings = (await db.execute(text("""
            SELECT count(*) FROM houseplant_care
            WHERE value_text LIKE '%:' AND length(value_text) < 30
        """))).scalar()
    print(f"заголовков рубрик к удалению: {headings}")

    if not args.apply:
        print("Ничего не изменено: для записи нужен --apply")
        return 0

    async with async_session() as db:
        for row_id, value in changed:
            await db.execute(text(
                "UPDATE houseplant_care SET value = CAST(:v AS jsonb) WHERE id = :id"),
                {"v": json.dumps(value, ensure_ascii=False), "id": row_id})
        await db.execute(text("""
            DELETE FROM houseplant_care
            WHERE value_text LIKE '%:' AND length(value_text) < 30
        """))
        await db.commit()
    print(f"Пересчитано строк: {len(changed)}, удалено заголовков: {headings}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
