"""Clean quest_places noise: remove micro-places (< _MIN_AREA_M2) and sub-features
(centroid inside a LARGER place) — but ONLY those WITHOUT a computed species-set, so
every place that proved biodiversity (★) survives regardless of size. quest_place_sets
cascade-delete. Audit dump first.
"""
import asyncio
import json

from sqlalchemy import text

from app.database import async_session

MIN_AREA_M2 = 50_000  # 0.05 km²

COND = """
  (
    p.area < :minarea
    OR EXISTS (SELECT 1 FROM quest_places b
               WHERE b.id<>p.id AND ST_Area(b.geom) > ST_Area(p.geom)
                 AND ST_Contains(b.geom, ST_Centroid(p.geom)))
  )
  AND NOT EXISTS (SELECT 1 FROM quest_place_sets ps WHERE ps.place_id = p.id)
"""


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(
            f"SELECT p.id, p.name, round((p.area/1e6)::numeric,3) FROM quest_places p WHERE {COND}"),
            {"minarea": MIN_AREA_M2})).all()
        ids = [str(r[0]) for r in rows]
        tot_before = (await s.execute(text("SELECT count(*) FROM quest_places"))).scalar()
        print(f"quest_places before: {tot_before} | to delete (noise/sub-features, no set): {len(ids)}")
        with open("/tmp/quest_places_deleted.json", "w", encoding="utf-8") as f:
            json.dump([{"id": str(r[0]), "name": r[1], "km2": float(r[2])} for r in rows], f, ensure_ascii=False)
        print("audit: /tmp/quest_places_deleted.json")
        # delete in batches (cascade handles quest_place_sets)
        for i in range(0, len(ids), 200):
            await s.execute(text("DELETE FROM quest_places WHERE id = ANY(CAST(:ids AS uuid[]))"),
                            {"ids": ids[i:i + 200]})
            await s.commit()
        tot_after = (await s.execute(text("SELECT count(*) FROM quest_places"))).scalar()
        print(f"quest_places after: {tot_after} (removed {tot_before - tot_after})")


asyncio.run(main())
