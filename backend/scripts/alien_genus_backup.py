"""Копия карточек до шага aliengenus (латынь чужого рода): JSON в stdout.

Берёт все видовые карточки растений с латынью из одного слова и, с --latin, карточки,
у которых уже стоит один из новых биномов (с ними шаг может слить карточку). Для
каждой строка plants целиком и очерк.

    docker compose exec -T -e PYTHONPATH=/app backend python - [--latin "Orlaya grandiflora;…"] \\
        < backend/scripts/alien_genus_backup.py > /root/backups/alien-genus-<дата>.json
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import text

from app.database import async_session
from app.services.plant_matching import _latin_key


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latin", default="", help="новые биномы через точку с запятой")
    args = parser.parse_args()
    keys = sorted({k for k in (_latin_key(x) for x in args.latin.split(";")) if k})
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT row_to_json(p)::text AS plant, m.monograph::text AS monograph
            FROM plants p LEFT JOIN plant_reader_monograph m ON m.plant_id = p.id
            WHERE (p.rank = 'species' AND p.kingdom = 'растение' AND btrim(p.name_latin) ~ '^[A-Z][a-z]+(\\s+[A-Z(].*)?$')
               OR lower(split_part(p.name_latin, ' ', 1) || ' ' || split_part(p.name_latin, ' ', 2)) = ANY(:k)"""),
            {"k": keys})).all()
    out = [{"plant": json.loads(r.plant), "monograph": json.loads(r.monograph) if r.monograph else None}
           for r in rows]
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(out, sys.stdout, ensure_ascii=False, default=str)
    print(f"карточек: {len(out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
