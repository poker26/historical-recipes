"""Приводит грибы к справочнику двойников и правилам уровней (services/fungi_safety).

Без ``--apply`` только показывает, что изменится. С ``--apply`` пишет, сохраняет
прежние значения в JSON (``--backup``) и сбрасывает кэш этих карточек на сайте.

    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/fungi_safety_fix.py \\
        [--apply --backup /tmp/fungi-safety-backup.json]
"""

import argparse
import asyncio
import json
import sys

from app.database import async_session
from app.services.fungi_safety import fix_fungi_cards


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", help="куда сохранить прежние значения")
    args = parser.parse_args()
    if args.apply and not args.backup:
        print("Для записи нужен --backup.")
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    async with async_session() as db:
        out = await fix_fungi_cards(db, apply=False)
        if args.apply:
            with open(args.backup, "w", encoding="utf-8") as f:
                json.dump(out["backup"], f, ensure_ascii=False, indent=1, default=str)
            out = await fix_fungi_cards(db, apply=True)
    for c in out["changed"]:
        lvl = f"L{c['level_from']}→L{c['level_to']}" if c["level_from"] != c["level_to"] else f"L{c['level_to']}"
        print(f"{lvl:9} {c['name_latin']} ({c['name']})")
        if c["twin_from"] != c["twin_to"]:
            print(f"    двойник: {c['twin_from']} → {c['twin_to']}")
        if c["confused_with"]:
            print(f"    путают {c['confused_with']}")
        if c["verdict_from"] != c["verdict_to"]:
            print(f"    было: {c['verdict_from']}")
            print(f"    стало: {c['verdict_to']}")
    for c in out["conflicts"]:
        print(f"РУКАМИ  {c['name']} | {c['name_latin']} | L{c['level']} | {c['why']} | {c['id']}")
    print(f"\nКарточек: {len(out['changed'])}, на ручной разбор: {len(out['conflicts'])}"
          + (" — записано" if args.apply else " — холостой прогон"))
    if args.apply and out["changed"]:
        from app.services.site_cache import revalidate_plants
        await revalidate_plants([c["id"] for c in out["changed"]])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
