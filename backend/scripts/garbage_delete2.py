"""Second garbage sweep: digit/description/abbrev-named cards the first regex missed
(name with a digit, «Род X 1. — …», a botanical DESCRIPTION captured as the name).
Delete the EMPTY ones (0 compounds / normalized uses / toxicities / harvests) — same
criteria + safety (audit, qdrant, cascade, recipe SET NULL) as the first sweep.
Cards WITH content are reported, not deleted.
"""
import asyncio
import json

from sqlalchemy import text

from app.database import async_session
from app.services import qdrant


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(r"""
            SELECT p.id, p.name, p.name_latin,
                   (SELECT count(*) FROM plant_compounds c WHERE c.plant_id=p.id) comp,
                   (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_id IS NOT NULL) ru,
                   (SELECT count(*) FROM plant_toxicities t WHERE t.plant_id=p.id) tox,
                   (SELECT count(*) FROM plant_harvests h WHERE h.plant_id=p.id) harv
            FROM plants p
            WHERE p.name ~ '[0-9]' OR p.name ~ '^[А-ЯA-Z][.]'
        """))).all()
    empty, content = [], []
    for pid, name, latin, comp, ru, tox, harv in rows:
        (empty if (comp == 0 and ru == 0 and tox == 0 and harv == 0) else content).append((str(pid), name))
    print(f"digit/abbrev cards: {len(rows)} | empty→delete: {len(empty)} | with content→kept: {len(content)}")
    if content:
        print("kept (with content):", [n for _, n in content][:20])
    ids = [e[0] for e in empty]
    with open("/tmp/garbage2_deleted.json", "w", encoding="utf-8") as f:
        json.dump([{"id": i, "name": n} for i, n in empty], f, ensure_ascii=False)
    print("audit: /tmp/garbage2_deleted.json")

    async with async_session() as s:
        refd = (await s.execute(text(
            "SELECT count(*) FROM recipe_ingredients WHERE plant_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids})).scalar()
    print(f"recipe_ingredients → SET NULL: {refd}")
    for i in range(0, len(ids), 256):
        await qdrant.delete_points("plants_v2", ids[i:i + 256])
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
    print(f"DONE. plants now: {tot}")


asyncio.run(main())
