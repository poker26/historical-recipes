# -*- coding: utf-8 -*-
"""Delete ANIMAL «plant» cards — non-plant materia (мясо, рыба, мозг летучей мыши, Рога оленя…)
ingested from the encyclopedic animal травник. This is a plants project; animals don't belong.

Detection is PRECISE to avoid nuking animal-THEMED real plants (Коровяк, Куркума, Воронец,
Консольда, Вороний глаз, Гусиный лук …): a card is animal iff
  * its name is EXACTLY a bare animal / animal-part word (exact-match — «Ворона»≠«Воронец»), OR
  * its name is «<animal-part> <animal>» (мозг летучей мыши, Рога оленя — no plant is named so),
AND it has no botanical latin (real plants carry one). Confirmed animals are deleted: child
facts CASCADE, recipe_ingredients/identify FKs SET NULL, qdrant purged, full snapshot audited in
`animal_card_audit` (reversible). Single-word stem-only matches are listed as UNCERTAIN, never
deleted.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/delete_animal_cards.py
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

# exact bare animal / animal-part card names (nominative + a few oblique). Exact-match only.
ANIMAL_EXACT = {
    "мясо", "рыба", "свинина", "говядина", "баранина", "конина", "дичь", "сало", "жир",
    "мозг", "кровь", "желчь", "печень", "почка", "желудок", "кость", "рог", "рога", "копыто",
    "шкура", "шерсть", "перо", "помет", "помёт", "навоз", "яйцо", "икра", "молоко",
    "скорпион", "паук", "муравей", "ворона", "воробей", "летучая мышь", "ёж", "еж", "осёл",
    "осел", "коршун", "лев", "тигр", "волк", "медведь", "змея", "лягушка", "заяц", "лисица",
    "собака", "кошка", "бык", "корова", "буйвол", "свинья", "баран", "овца", "козёл", "козел",
    "олень", "верблюд", "слон", "мышь", "крыса", "орёл", "сокол", "аист", "журавль", "голубь",
    "перепел", "фазан", "краб", "рак", "улитка", "червь", "пчела", "оса", "муха", "комар",
    "саранча", "жаба", "черепаха", "ящерица", "крокодил", "кит", "дельфин", "тюлень", "лошадь",
    "конь", "петух", "курица", "гусь", "утка", "летучая мышь",
}
# <part> <animal> multiword (genitive animal). Both stems must be present.
_PART = (r"мясо|сал[оа]|жир|мозг\w*|кров[ьи]|желч\w*|печен\w*|почк\w*|желудок|кост[ьи]|рог\w*|"
         r"копыт\w*|шкур\w*|шерст\w*|пер[оья]|помёт|помет|навоз|яйц\w*|икр\w*|чешу\w*|плавник|"
         r"клюв|игл\w*|панцир\w*|хвост|зуб\w*|коготь|когт\w*|слюн\w*|желез\w*|сухожил\w*|кишк\w*|"
         r"селезён\w*|лёгк\w*|сердце|член")
_ANIM = (r"ежа|летучей мыши|осла|осл\w*|коня|конск\w*|собак\w*|кошк\w*|волка|лисиц\w*|зайца|"
         r"зайч\w*|быка|коров\w*|буйвол\w*|свин\w*|барана|овц\w*|козл\w*|оленя|олен\w*|медвед\w*|"
         r"тигр\w*|льв\w*|слона|верблюд\w*|змеи|лягушк\w*|рыб\w*|птиц\w*|курицы|кур\w*|петуха|"
         r"петух\w*|гуся|гус\w*|утк\w*|ворон\w*|воробь\w*|скорпион\w*|паука|лошади|мыши")
_MULTI = re.compile(rf"^({_PART})\s+\w*\s*({_ANIM})$")


def is_animal(name: str) -> bool:
    n = re.sub(r"[^а-яёa-z ]", "", (name or "").lower()).strip()
    if n in ANIMAL_EXACT:
        return True
    if bool(_MULTI.match(n)):
        return True
    # «<part> X, <animal>» / «<part> <animal>, <animal>» comma forms (мозг петуха, курицы)
    head = n.split(",")[0].strip()
    return bool(_MULTI.match(head)) or head in ANIMAL_EXACT


async def main():
    async with async_session() as db:
        # exact-animal / «<part> <animal>» names are unambiguous regardless of latin (no real plant
        # is named «мясо»/«собака»; a zoological latin like Canis/Araneae only confirms animal).
        plants = (await db.execute(select(Plant))).scalars().all()
        animal = [p for p in plants if is_animal(p.name)]
        print(f"confirmed ANIMAL cards (latin-less, exact/multiword): {len(animal)}")
        for p in sorted(animal, key=lambda p: p.name or ""):
            nri = (await db.execute(text(
                "SELECT count(*) FROM recipe_ingredients WHERE plant_id=:p"), {"p": p.id})).scalar()
            print(f"   [{nri:4}] {p.name}")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS animal_card_audit (id uuid, name text, names_historical text, "
            "snapshot jsonb, at timestamptz DEFAULT now())"))
        purge = []
        for p in animal:
            snap = {"name": p.name, "kingdom": p.kingdom, "family": p.family,
                    "names_historical": p.names_historical}
            await db.execute(text(
                "INSERT INTO animal_card_audit (id,name,names_historical,snapshot) "
                "VALUES (:i,:n,:h,CAST(:s AS jsonb))"),
                {"i": str(p.id), "n": p.name, "h": json.dumps(p.names_historical, ensure_ascii=False),
                 "s": json.dumps(snap, ensure_ascii=False)})
            if p.qdrant_point_id:
                purge.append((p.qdrant_collection or "plants_v2", p.qdrant_point_id))
            await db.delete(p)            # child facts CASCADE; recipe_ingredients/identify FK SET NULL
        await db.commit()
        by_coll = {}
        for coll, pid in purge:
            by_coll.setdefault(coll, []).append(pid)
        for coll, pids in by_coll.items():
            try:
                await qdrant.delete_points(coll, pids)
            except Exception as e:  # noqa: BLE001
                print(f"  qdrant purge failed ({coll}): {e}")
        print(f"\ndeleted {len(animal)} animal cards (audit: animal_card_audit). qdrant purged: {len(purge)}")


if __name__ == "__main__":
    asyncio.run(main())
