# -*- coding: utf-8 -*-
"""Comprehensive sweep — unlink MINERAL/CHEMICAL ingredients from plants + delete mineral cards.

Third class after animal (fix_animal_mislinks/delete_animal_cards) and dairy (fix_dairy_mislinks):
soluble/mineral materia (соль, сода, квасцы, купорос, сера, свинец, ртуть, нашатырь, известь,
мышьяк…) wrongly matched to plant cards. A plants project shouldn't carry minerals.

PLANT-DERIVED chemicals are deliberately EXCLUDED (kept): дёготь (birch tar), смола/живица (resin),
скипидар (turpentine), камфора, масло <plant>, сок/отвар/настой/экстракт <plant>. The lexicon is
TRUE inorganic minerals + named acids/salts only.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/nonplant_mineral_sweep.py
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

# inorganic minerals / metals / named chemicals (NON-plant). Word-ish boundaries to avoid plant
# stems (e.g. «серая» plant ≠ «сера»; «мелисса» ≠ «мел»).
RX_MINERAL = (
    r"\bсол[ьяи]\b|поваренн\w* сол|морск\w* сол|глауберов\w* сол|\bсода\b|питьев\w* сода|"
    r"квасц\w*|купорос\w*|\bсера\b|\bсеры\b|серн\w* цвет|\bсвинец\b|\bсвинца\b|свинцов\w* белил|"
    r"свинцов\w* сахар|свинцов\w* примоч|свинцов\w* вод|\bртут[ьи]\b|ртутн\w*|"
    r"нашатыр\w*|\bизвест[ьи]\b|негашён\w* извест|гашён\w* извест|\bмел\b|\bмела\b|\bгипс\w*|"
    r"\bмышьяк\w*|сурьм\w*|\bбура\b|\bбуры\b|селитр\w*|поташ\w*|винн\w* камень|магнези\w*|"
    r"\bаммиак\w*|кинова[рь]\w*|\bохра\b|свинцов\w* белил|\bбелила\b|купорос\w* масло|"
    r"\bвисмут\w*|\bкупорос|железн\w* опилк|медн\w* опилк|золотн\w*|серебрян\w* пыл|"
    r"солян\w* кислот|серн\w* кислот|азотн\w* кислот|царск\w* водк|\bмумиё\b|\bмумие\b|"
    r"\bглина\b|\bглины\b|болюс|\bтальк\w*|\bсуриик|\bсурик\w*")
# exact non-plant mineral card names (no plant collision).
MINERAL_CARD = {
    "соль", "поваренная соль", "морская соль", "сода", "квасцы", "купорос", "медный купорос",
    "железный купорос", "сера", "свинец", "ртуть", "нашатырь", "нашатырный спирт", "известь",
    "негашёная известь", "мел", "гипс", "мышьяк", "сурьма", "бура", "селитра", "поташ",
    "винный камень", "магнезия", "аммиак", "киноварь", "циннабарис", "охра", "белила",
    "свинцовые белила", "висмут", "мумиё", "мумие", "глина", "болюс", "тальк", "сурик",
    "купоросное масло", "квасцы жжёные", "жжёные квасцы",
    "золото", "серебро", "медь", "железо", "олово", "цинк", "свинцовый сахар",
    "сернистая сурьма", "сурьма сернистая",
}


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT ri.id::text, ri.plant_id::text, ri.name, p.name FROM recipe_ingredients ri "
            "JOIN plants p ON p.id=ri.plant_id WHERE lower(ri.name) ~ :rx"), {"rx": RX_MINERAL})).all()
        print(f"mineral/chemical ingredients linked to a plant — to unlink: {len(rows)}")
        seen = set()
        for _, _, inm, pn in rows:
            k = (inm, pn)
            if k not in seen:
                seen.add(k)
                if len(seen) <= 22:
                    print(f"   «{(inm or '')[:30]:30}» -> «{pn[:24]}»")

        plants = (await db.execute(select(Plant))).scalars().all()
        cards = [p for p in plants
                 if re.sub(r"[^а-яёa-z ]", "", (p.name or "").lower()).strip() in MINERAL_CARD]
        print(f"\nmineral «plant» cards to delete (exact-named, non-plant): {len(cards)}")
        for p in cards:
            nri = (await db.execute(text("SELECT count(*) FROM recipe_ingredients WHERE plant_id=:p"),
                                    {"p": p.id})).scalar()
            print(f"   «{p.name}» (latin={p.name_latin}) — {nri} links")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS nonplant_mislink_audit (ri_id uuid, old_plant_id uuid, "
            "ingredient_name text, plant_name text, klass text, at timestamptz DEFAULT now())"))
        for rid, opid, inm, pn in rows:
            await db.execute(text(
                "INSERT INTO nonplant_mislink_audit (ri_id,old_plant_id,ingredient_name,plant_name,klass) "
                "VALUES (CAST(:r AS uuid),CAST(:o AS uuid),:i,:p,'mineral')"),
                {"r": rid, "o": opid, "i": inm, "p": pn})
        if rows:
            await db.execute(text(
                "UPDATE recipe_ingredients SET plant_id=NULL WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [r[0] for r in rows]})
        purge = []
        for p in cards:
            await db.execute(text(
                "INSERT INTO animal_card_audit (id,name,names_historical,snapshot) "
                "VALUES (:i,:n,:h,CAST(:s AS jsonb))"),
                {"i": str(p.id), "n": p.name,
                 "h": json.dumps(p.names_historical, ensure_ascii=False),
                 "s": json.dumps({"name": p.name, "kind": "mineral", "latin": p.name_latin},
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
        print(f"\nunlinked {len(rows)} mineral ingredients | deleted {len(cards)} mineral cards "
              "(audit: nonplant_mislink_audit + animal_card_audit).")


if __name__ == "__main__":
    asyncio.run(main())
