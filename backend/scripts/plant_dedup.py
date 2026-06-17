"""plant.duplicate check + merge. Group cards by their latin BINOMIAL key (genus +
species, author/case stripped). A group of >1 = the same species split across cards
(Aconitum napellus ×4). Keep the richest card (tiebreak: has a Russian name), repoint
ALL content + recipe/identify links from the dups into it, delete the dups + qdrant.
Genus-only latin (Dryopteris) is NOT merged — only binomials.

Usage:  python plant_dedup.py pilot [N]   — show proposed merges, NO writes
        python plant_dedup.py             — apply (repoint + delete), batched
"""
import asyncio
import re
import sys

from sqlalchemy import text

from app.database import async_session
from app.services import qdrant

_LAT = re.compile(r"[A-Za-z]+")
_CYR = re.compile(r"[А-Яа-яЁё]")
CHILD = ["plant_compounds", "plant_medicinal_uses", "plant_habitats", "plant_book_mentions",
         "plant_toxicities", "plant_culinary_uses", "plant_harvests", "plant_properties"]
REPOINT = [("recipe_ingredients", "plant_id"), ("ingredients", "plant_id"),
           ("essential_oils", "plant_id"), ("identifications", "matched_plant_id")]


def bino_key(latin):
    t = _LAT.findall(latin or "")
    return f"{t[0].lower()} {t[1].lower()}" if len(t) >= 2 else None


async def build_groups(s):
    rows = (await s.execute(text("""
        SELECT p.id, p.name, p.name_latin,
               (SELECT count(*) FROM plant_compounds c WHERE c.plant_id=p.id)
             + (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id=p.id)
             + (SELECT count(*) FROM plant_toxicities t WHERE t.plant_id=p.id)
             + (SELECT count(*) FROM plant_harvests h WHERE h.plant_id=p.id)
             + (SELECT count(*) FROM plant_book_mentions m WHERE m.plant_id=p.id) AS score
        FROM plants p WHERE p.name_latin IS NOT NULL AND p.name_latin <> ''
    """))).all()
    groups: dict = {}
    for pid, name, latin, score in rows:
        k = bino_key(latin)
        if k:
            groups.setdefault(k, []).append({"id": str(pid), "name": name, "latin": latin,
                                             "score": score or 0, "cyr": bool(_CYR.search(name or ""))})
    # keep only real duplicate groups; primary = richest, tiebreak has-russian, then stable id
    dups = {}
    for k, cards in groups.items():
        if len(cards) > 1:
            cards.sort(key=lambda c: (-c["score"], not c["cyr"], c["id"]))
            dups[k] = cards
    return dups


async def main():
    pilot = len(sys.argv) > 1 and sys.argv[1] == "pilot"
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 15)
    async with async_session() as s:
        dups = await build_groups(s)
    total_extra = sum(len(c) - 1 for c in dups.values())
    print(f"duplicate groups: {len(dups)} | redundant cards to merge away: {total_extra}")
    if pilot:
        shown = sorted(dups.items(), key=lambda kv: -len(kv[1]))[:n]
        for k, cards in shown:
            p = cards[0]
            print(f"\n[{k}] keep: {p['name']!r} ({p['latin']!r}) score={p['score']}")
            for d in cards[1:]:
                print(f"      merge← {d['name']!r} ({d['latin']!r}) score={d['score']}")
        return

    # audit dump of the full merge plan (recoverable record of who merged into whom)
    import json
    with open("/tmp/plant_dedup.json", "w", encoding="utf-8") as f:
        json.dump({k: {"keep": c[0], "merge": c[1:]} for k, c in dups.items()}, f, ensure_ascii=False)
    print("audit written: /tmp/plant_dedup.json")

    # APPLY: repoint children + links into the primary, delete dups + qdrant point
    merged = 0
    dead_ids = []
    for k, cards in dups.items():
        primary = cards[0]["id"]
        for d in cards[1:]:
            dup = d["id"]
            async with async_session() as s:
                for tbl in CHILD:
                    await s.execute(text(f"UPDATE {tbl} SET plant_id=CAST(:p AS uuid) WHERE plant_id=CAST(:d AS uuid)"),
                                    {"p": primary, "d": dup})
                for tbl, col in REPOINT:
                    await s.execute(text(f"UPDATE {tbl} SET {col}=CAST(:p AS uuid) WHERE {col}=CAST(:d AS uuid)"),
                                    {"p": primary, "d": dup})
                await s.execute(text("DELETE FROM plants WHERE id=CAST(:d AS uuid)"), {"d": dup})
                await s.commit()
            dead_ids.append(dup)
            merged += 1
            if len(dead_ids) >= 200:
                await qdrant.delete_points("plants_v2", dead_ids); dead_ids = []
            if merged % 100 == 0:
                print(f"  merged {merged}/{total_extra}", flush=True)
    if dead_ids:
        await qdrant.delete_points("plants_v2", dead_ids)
    async with async_session() as s:
        tot = (await s.execute(text("SELECT count(*) FROM plants"))).scalar()
    print(f"DONE. merged={merged} | plants now: {tot}")


asyncio.run(main())
