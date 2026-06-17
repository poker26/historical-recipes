"""Delete the unambiguous-garbage plant cards: broken/OCR/abbreviated name AND no
substantive content (0 compounds, 0 normalized medicinal uses, 0 toxicities, 0
harvests). Clean-latin-named cards are KEPT (valid identity); rich-but-broken cards
are KEPT for re-ID. FK cascades drop child rows; recipe_ingredients/identify SET
NULL. Qdrant points (plants_v2, point_id = plant_id) deleted too. Audit dump first.
"""
import asyncio
import json
import re

from sqlalchemy import text

from app.database import async_session
from app.services import qdrant

JUNK = re.compile(r"[\$@{}\[\]|<>0-9!]")
CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")
CLEAN_BINO = re.compile(r"^[A-Z][a-z]{2,}\s+[a-z]{2,}")
CLEAN_GENUS = re.compile(r"^[A-Z][a-z]{2,}\.?$")
ABBREV = re.compile(r"^[А-ЯA-Z]\.\s*\S")
DELETE_BUCKETS = {"ocr_junk", "abbrev_genus", "mixed_script", "other_messy"}


def name_bucket(name):
    n = (name or "").strip()
    has_cyr, has_lat, has_junk = bool(CYR.search(n)), bool(LAT.search(n)), bool(JUNK.search(n))
    if has_junk:
        return "ocr_junk"
    if ABBREV.match(n):
        return "abbrev_genus"
    if not has_cyr and CLEAN_BINO.match(n):
        return "clean_latin_binomial"
    if not has_cyr and CLEAN_GENUS.match(n):
        return "clean_latin_genus"
    if has_cyr and has_lat:
        return "mixed_script"
    return "other_messy"


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(r"""
            SELECT p.id, p.name, p.name_latin, p.description,
                   (SELECT count(*) FROM plant_compounds c WHERE c.plant_id=p.id) comp,
                   (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_id IS NOT NULL) ru,
                   (SELECT count(*) FROM plant_toxicities t WHERE t.plant_id=p.id) tox,
                   (SELECT count(*) FROM plant_harvests h WHERE h.plant_id=p.id) harv
            FROM plants p
            WHERE p.name ~ '[A-Za-z]' OR p.name ~ '[\$@{}\[\]|<>]' OR p.name ~ '^[А-ЯA-Z]\.'
        """))).all()

    delete = []
    for pid, name, latin, desc, comp, ru, tox, harv in rows:
        if name_bucket(name) not in DELETE_BUCKETS:
            continue
        if comp == 0 and ru == 0 and tox == 0 and harv == 0:
            delete.append((str(pid), name, latin, desc))
    ids = [d[0] for d in delete]
    print(f"garbage-named scanned: {len(rows)} | DELETE set (empty+broken): {len(ids)}")

    # audit dump (recoverable record of exactly what was removed)
    with open("/tmp/garbage_deleted.json", "w", encoding="utf-8") as f:
        json.dump([{"id": i, "name": n, "latin": l, "description": d} for i, n, l, d in delete],
                  f, ensure_ascii=False)
    print("audit written: /tmp/garbage_deleted.json")

    # how many referenced by recipe ingredients (will SET NULL, harmless)
    async with async_session() as s:
        refd = (await s.execute(text(
            "SELECT count(*) FROM recipe_ingredients WHERE plant_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids})).scalar()
    print(f"recipe_ingredients referencing these (→ SET NULL): {refd}")

    # delete qdrant points (point_id == plant_id); ignores non-existent ids
    for i in range(0, len(ids), 256):
        await qdrant.delete_points("plants_v2", ids[i:i + 256])
    print("qdrant points removed")

    # delete plants in batches (FK cascade handles children)
    deleted = 0
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        async with async_session() as s:
            await s.execute(text("DELETE FROM plants WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": chunk})
            await s.commit()
        deleted += len(chunk)
        print(f"  deleted {deleted}/{len(ids)}", flush=True)

    async with async_session() as s:
        tot = (await s.execute(text("SELECT count(*) FROM plants"))).scalar()
        garb = (await s.execute(text(
            r"SELECT count(*) FROM plants WHERE name ~ '[A-Za-z]' OR name ~ '[\$@{}\[\]|<>]' OR name ~ '^[А-ЯA-Z]\.'"))).scalar()
    print(f"DONE. plants now: {tot} | garbage-named remaining: {garb}")


asyncio.run(main())
