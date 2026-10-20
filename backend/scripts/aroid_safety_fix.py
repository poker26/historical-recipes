"""Приводит карточки ароидных к правилу безопасности (edible_safety.aroid_rule).

Без ``--apply`` только показывает, что изменится. С ``--apply`` пишет и сохраняет
прежние значения в JSON (``--backup``), чтобы их можно было вернуть.

    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/aroid_safety_fix.py \\
        [--apply --backup /tmp/aroid-safety-backup.json]
"""

import argparse
import asyncio
import json
import sys

from app.database import async_session
from app.services.aroid_safety import fix_aroid_cards


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
        out = await fix_aroid_cards(db, apply=False)
        if args.apply:
            with open(args.backup, "w", encoding="utf-8") as f:
                json.dump(out["backup"], f, ensure_ascii=False, indent=1, default=str)
            out = await fix_aroid_cards(db, apply=True)
    for c in out["changed"]:
        print(f"L{c['from']} → L{c['to']}  {c['name_latin']} ({c['name']})")
        if c["verdict_from"] != c["verdict_to"]:
            print(f"    было: {c['verdict_from']}")
            print(f"    стало: {c['verdict_to']}")
    print(f"\nКарточек: {len(out['changed'])}" + (" — записано" if args.apply else " — холостой прогон"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
