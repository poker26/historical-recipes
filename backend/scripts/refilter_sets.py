"""One-time corpus-only re-filter of EXISTING quest_place_sets — pure DB (no iNat):
drop species without a corpus monograph from species_set/species_meta, recompute the
target. A set that drops below 5 corpus species is deleted (no badge). Idempotent.
"""
import asyncio
import json

from sqlalchemy import text

from app.database import async_session
from app.services.plant_matching import resolve_latin_to_plants


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(
            "SELECT id, species_set, species_meta, target FROM quest_place_sets WHERE species_set IS NOT NULL"))).all()
    updated = deleted = unchanged = 0
    for sid, sset, meta, target in rows:
        keys = list(sset or [])
        async with async_session() as s:
            plant_map = await resolve_latin_to_plants(s, keys)
            new_keys = [k for k in keys if plant_map.get(k)]
            new_meta = [m for m in (meta or []) if plant_map.get(m.get("key"))]
            if len(new_keys) == len(keys):
                unchanged += 1
                continue
            if len(new_keys) < 5:
                await s.execute(text("DELETE FROM quest_place_sets WHERE id=:i"), {"i": str(sid)})
                deleted += 1
            else:
                new_target = max(5, min(15, round(0.6 * len(new_keys))))
                await s.execute(text(
                    "UPDATE quest_place_sets SET species_set=:ss, species_meta=CAST(:mm AS jsonb), target=:t WHERE id=:i"),
                    {"ss": new_keys, "mm": json.dumps(new_meta, ensure_ascii=False), "t": new_target, "i": str(sid)})
                updated += 1
            await s.commit()
    print(f"sets: {len(rows)} | updated(corpus-filtered)={updated} | deleted(<5 corpus)={deleted} | unchanged={unchanged}")


asyncio.run(main())
