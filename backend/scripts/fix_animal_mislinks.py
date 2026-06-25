# -*- coding: utf-8 -*-
"""Unlink ANIMAL-part ingredients wrongly matched to plants (the «Ежа сборная» = hedgehog bug).

The encyclopedic animal травник (§1549 Гунфуз=еж, летучая мышь, осёл…) yields ingredients like
«мясо ежа», «кровь оленя», «желчь ежа». The plant matcher links them to a plant via a homonym /
shared token: «Ежа сборная» (Dactylis, folk-alias «ежа») ← «мясо ежа» (ёж=hedgehog); «Драконова
кровь» (resin plant) ← «кровь оленя» (the «кровь» token). These are ANIMAL materia — they must not
link to a plant card. Set plant_id=NULL for them (audit `animal_mislink_audit`, reversible). Also
lists the junk ANIMAL «plant» cards (мясо, мозг летучей мыши, Помет летучей мыши…) for disposition.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/fix_animal_mislinks.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import os

from sqlalchemy import text

from app.database import async_session

APPLY = bool(os.environ.get("APPLY"))

# «<animal-part> <animal>» — an animal body part of a named animal. The animal list is the tell
# (a PLANT part like «корень/лист» never follows these with an animal genitive).
PART = r"(мясо|сало|шкур\w*|желч\w*|жир|мозг\w*|печен\w*|почк\w*|желудок|рог\w*|копыт\w*|перо|перья|шерст\w*|помет|навоз|кровь|сухожил\w*|молоко|яйцо|яйца|икра|чешуя|плавник|клюв|кость|кости|сердце|лёгк\w*|селезён\w*|игл\w*|панцир\w*|хвост|ус|усы|зуб\w*|коготь|когт\w*|слюна|желез\w*)"
ANIMAL = r"(ежа|летучей мыши|осла|осли\w*|кон\w*|собак\w*|кошк\w*|волка|лисиц\w*|зайца|зайч\w*|быка|коров\w*|буйвол\w*|свин\w*|барана|овц\w*|козл\w*|козлёнка|козлен\w*|оленя|олен\w*|медвед\w*|тигр\w*|льв\w*|слона|верблюд\w*|змеи|змеин\w*|лягушк\w*|рыб\w*|птиц\w*|курицы|кур\w*|петуха|петух\w*|гуся|гус\w*|утк\w*|ворон\w*|воробь\w*|скорпион\w*|паук\w*|муравь\w*|морского ежа)"
RX_MISLINK = f"{PART} {ANIMAL}"
RX_ANIMAL_CARD = f"^{PART} {ANIMAL}|^мясо$|^{ANIMAL}$"


async def main():
    async with async_session() as db:
        # FIX TARGET: animal-part ingredient on a REAL plant (has a botanical latin) — «Ежа сборная»
        # = Dactylis, «Драконова кровь» = Dracaena, «Гриб баран» = Grifola. (Cards WITH no latin that
        # are themselves animal entries are a separate junk-card disposition, below — not unlinked.)
        rows = (await db.execute(text(
            "SELECT ri.id::text, ri.plant_id::text, ri.name, p.name FROM recipe_ingredients ri "
            "JOIN plants p ON p.id=ri.plant_id WHERE lower(ri.name) ~ :rx AND p.name_latin IS NOT NULL"),
            {"rx": RX_MISLINK})).all()
        print(f"animal-part ingredients on REAL (latin'd) plants — to unlink: {len(rows)}")
        for _, _, inm, pn in rows[:14]:
            print(f"   «{(inm or '')[:30]:30}» -> «{pn[:26]}»")

        # junk ANIMAL cards = latin-less card whose NAME is an animal part/animal (precise — real
        # plants Коровяк/Куркума/Конопля all carry a latin so they're excluded).
        cards = (await db.execute(text(
            "SELECT p.id::text, p.name, "
            "  (SELECT count(*) FROM recipe_ingredients ri WHERE ri.plant_id=p.id) nri "
            "FROM plants p WHERE p.name_latin IS NULL AND lower(p.name) ~ :rx ORDER BY 3 DESC"),
            {"rx": RX_ANIMAL_CARD})).all()
        print(f"\njunk ANIMAL «plant» cards (latin-less, non-plant — for separate disposition): {len(cards)}")
        for _, nm, nri in cards[:15]:
            print(f"   «{(nm or '')[:30]:30}» | {nri} recipe-links")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS animal_mislink_audit (ri_id uuid, old_plant_id uuid, "
            "ingredient_name text, plant_name text, at timestamptz DEFAULT now())"))
        ids = [rid for rid, *_ in rows]
        for rid, opid, inm, pn in rows:
            await db.execute(text(
                "INSERT INTO animal_mislink_audit (ri_id, old_plant_id, ingredient_name, plant_name) "
                "VALUES (CAST(:r AS uuid), CAST(:o AS uuid), :i, :p)"),
                {"r": rid, "o": opid, "i": inm, "p": pn})
        await db.execute(text(
            "UPDATE recipe_ingredients SET plant_id=NULL WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids})
        await db.commit()
        print(f"\nunlinked {len(rows)} animal-part ingredients from REAL plants (audit: animal_mislink_audit).")


if __name__ == "__main__":
    asyncio.run(main())
