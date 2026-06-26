# -*- coding: utf-8 -*-
"""Unlink DAIRY ingredients homonym-matched to plants + delete dairy «plant» cards.

«коровье масло» (cow BUTTER) matched «Льнянка обыкновенная» (Linaria) — toadflax carries the
legit historical folk-alias «Коровье масло» (butter-yellow flowers), which collides with the
dairy ingredient. 30 butter recipes landed on the plant. Same class: «сливочное масло»→Лук,
«сметана/простокваша»→a dairy card. Dairy is animal-derived — not a plant; this is a plants
project.

PRECISE so plant oils survive: cow = «коровь\\w*» (с ь), NOT «коровяк» (mullein, «коровя…») — so
«масло коровяка» (mullein oil → Коровяк) is SPARED; vegetable oils (облепиховое/льняное масло)
never match. Unlinks dairy ingredients on plants (audit `dairy_mislink_audit`) and deletes
exact-named dairy cards (Простокваша, Сметана, Творог…). Reversible.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/fix_dairy_mislinks.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import json
import os
import re

from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant
from app.services import qdrant

APPLY = bool(os.environ.get("APPLY"))

# dairy ingredient: cow/cream butter, animal fat, milk products. «коровь\w*» (cow) not «коровяк».
RX_DAIRY = (r"коровь\w* масло|масло коровь\w*|сливочн\w* масло|масло сливочн\w*|топлё?н\w* масло|"
            r"масло топлё?н\w*|чухонское масло|русское масло|коровь\w* жир|сметан\w*|простокваш\w*|"
            r"\bтворог|\bсливки\b|козье молоко|кобылье молоко|коровье молоко|пахта\b")
# exact-named dairy cards (non-plant). exact-match — no plant is named exactly these.
DAIRY_CARD = {"простокваша", "сметана", "творог", "сливки", "коровье масло", "сливочное масло",
              "топленое масло", "топлёное масло", "пахта", "сыворотка", "сыр", "брынза", "кефир",
              "молоко", "масло коровье", "масло сливочное"}


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT ri.id::text, ri.plant_id::text, ri.name, p.name FROM recipe_ingredients ri "
            "JOIN plants p ON p.id=ri.plant_id WHERE lower(ri.name) ~ :rx"), {"rx": RX_DAIRY})).all()
        print(f"dairy ingredients linked to a plant — to unlink: {len(rows)}")
        for _, _, inm, pn in rows[:12]:
            print(f"   «{(inm or '')[:30]:30}» -> «{pn[:26]}»")

        plants = (await db.execute(select(Plant))).scalars().all()
        cards = [p for p in plants
                 if re.sub(r"[^а-яёa-z ]", "", (p.name or "").lower()).strip() in DAIRY_CARD]
        print(f"\ndairy «plant» cards to delete (exact-named, non-plant): {len(cards)}")
        for p in cards:
            nri = (await db.execute(text("SELECT count(*) FROM recipe_ingredients WHERE plant_id=:p"),
                                    {"p": p.id})).scalar()
            print(f"   «{p.name}» (latin={p.name_latin}) — {nri} links")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS dairy_mislink_audit (ri_id uuid, old_plant_id uuid, "
            "ingredient_name text, plant_name text, at timestamptz DEFAULT now())"))
        for rid, opid, inm, pn in rows:
            await db.execute(text(
                "INSERT INTO dairy_mislink_audit (ri_id,old_plant_id,ingredient_name,plant_name) "
                "VALUES (CAST(:r AS uuid),CAST(:o AS uuid),:i,:p)"),
                {"r": rid, "o": opid, "i": inm, "p": pn})
        if rows:
            await db.execute(text(
                "UPDATE recipe_ingredients SET plant_id=NULL WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [r[0] for r in rows]})

        purge = []
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS animal_card_audit (id uuid, name text, names_historical text, "
            "snapshot jsonb, at timestamptz DEFAULT now())"))
        for p in cards:
            await db.execute(text(
                "INSERT INTO animal_card_audit (id,name,names_historical,snapshot) "
                "VALUES (:i,:n,:h,CAST(:s AS jsonb))"),
                {"i": str(p.id), "n": p.name,
                 "h": json.dumps(p.names_historical, ensure_ascii=False),
                 "s": json.dumps({"name": p.name, "kind": "dairy", "latin": p.name_latin},
                                 ensure_ascii=False)})
            if p.qdrant_point_id:
                purge.append((p.qdrant_collection or "plants_v2", p.qdrant_point_id))
            await db.delete(p)
        await db.commit()
        by = {}
        for c, pid in purge:
            by.setdefault(c, []).append(pid)
        for c, pids in by.items():
            try:
                await qdrant.delete_points(c, pids)
            except Exception as e:  # noqa: BLE001
                print(f"  qdrant purge failed: {e}")
        print(f"\nunlinked {len(rows)} dairy ingredients | deleted {len(cards)} dairy cards "
              f"(audit: dairy_mislink_audit + animal_card_audit).")


if __name__ == "__main__":
    asyncio.run(main())
