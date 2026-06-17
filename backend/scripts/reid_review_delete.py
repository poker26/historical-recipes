"""Delete the reid-review residue: rich-but-broken cards that the full re-ID pass
(LLM full-context + GBIF/DB) could NOT confidently identify even to genus. Their
content (a few compounds on an unidentifiable plant) is unattributable → low value;
we have enough data. FK cascades drop children; recipe_ingredients/identify SET
NULL; qdrant points removed; the reid findings are cleared. Audit dump first.
"""
import asyncio
import json

from sqlalchemy import text

from app.database import async_session
from app.services import qdrant

CHECK = "identity.reid_broken"


async def main():
    async with async_session() as s:
        rows = (await s.execute(text("""
            SELECT p.id, p.name, p.name_latin,
                   (SELECT count(*) FROM plant_compounds c WHERE c.plant_id=p.id) comp
            FROM plants p
            WHERE p.id::text IN (SELECT entity_id FROM data_quality_findings
                                 WHERE check_id=:c AND status='open')
        """), {"c": CHECK})).all()
    ids = [str(r[0]) for r in rows]
    print(f"reid-review cards to delete: {len(ids)}")
    with open("/tmp/reid_review_deleted.json", "w", encoding="utf-8") as f:
        json.dump([{"id": str(r[0]), "name": r[1], "latin": r[2], "compounds": r[3]} for r in rows],
                  f, ensure_ascii=False)
    print("audit written: /tmp/reid_review_deleted.json")

    async with async_session() as s:
        refd = (await s.execute(text(
            "SELECT count(*) FROM recipe_ingredients WHERE plant_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids})).scalar()
    print(f"recipe_ingredients → SET NULL: {refd}")

    for i in range(0, len(ids), 256):
        await qdrant.delete_points("plants_v2", ids[i:i + 256])
    print("qdrant points removed")

    deleted = 0
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        async with async_session() as s:
            await s.execute(text(
                "DELETE FROM data_quality_findings WHERE check_id=:c AND entity_id = ANY(:e)"),
                {"c": CHECK, "e": chunk})
            await s.execute(text("DELETE FROM plants WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": chunk})
            await s.commit()
        deleted += len(chunk)
        print(f"  deleted {deleted}/{len(ids)}", flush=True)

    async with async_session() as s:
        tot = (await s.execute(text("SELECT count(*) FROM plants"))).scalar()
    print(f"DONE. plants now: {tot}")


asyncio.run(main())
