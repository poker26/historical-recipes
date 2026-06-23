"""Corpus-wide recipe dedup — collapse genuine (name + text) clones to one canonical row.

FINDING (2026-06-23): the corpus has essentially NO true duplicates. Fingerprinting on
original_text ALONE flags 1206 groups / ~1826 rows — but EVERY one is multi-name (0 same-name
groups). These are NOT re-ingestion artifacts: encyclopedic / classical травники (e.g. «$1298
коршун…») store the whole SOURCE PASSAGE as each extracted remedy's original_text, so distinct
recipes («Зола перьев коршуна» vs «Мозг коршуна с пореем») share one text. Deleting by
text-only would DESTROY distinct recipes. Keyed on name+text the dup count is ~0 — the pipeline
is idempotent. (The repetitive shared-passage body is a #2-structuring problem: distil a
per-recipe `normalized_text` — NOT a dedup problem.)

So this de-dups on NAME+text and currently finds ~nothing; it stays as the safe, reusable tool
should a future ingest produce real clones. Hard-deletes the non-canonical rows but snapshots
every deleted row + its ingredients into `recipe_dedup_audit` first (reversible).
recipe_ingredients are ON DELETE CASCADE; each recipe carries its own qdrant_point_id/collection
so the vector is removed precisely. Canonical = most plant-linked ingredients → has chunk_id →
oldest → lowest id.

    DRY_RUN=1 docker compose exec -T -e PYTHONPATH=/app backend python scripts/dedup_recipes.py   # report only
            docker compose exec -T -e PYTHONPATH=/app backend python scripts/dedup_recipes.py     # execute
"""
import asyncio
import os

from sqlalchemy import text

from app.database import async_session
from app.services import qdrant

DRY = bool(os.environ.get("DRY_RUN"))
# name+text — text alone conflates shared-passage extractions (see module docstring).
NORM = r"(btrim(lower(name)) || '|' || btrim(lower(regexp_replace(original_text, '\s+', ' ', 'g'))))"


async def main():
    async with async_session() as db:
        if not DRY:
            await db.execute(text(
                "CREATE TABLE IF NOT EXISTS recipe_dedup_audit ("
                "  id uuid PRIMARY KEY, canonical_id uuid, fingerprint text, book_id uuid, "
                "  chunk_id uuid, name text, category text, original_text text, "
                "  qdrant_point_id text, qdrant_collection text, ingredients_json jsonb, "
                "  deleted_at timestamptz DEFAULT now())"))
            await db.commit()

        # rank rows within each identical-text group; rn=1 is the canonical survivor.
        ranked = (await db.execute(text(
            "WITH grp AS ("
            "  SELECT r.id, r.book_id, r.chunk_id, r.name, r.category, r.original_text, "
            "         r.qdrant_point_id, r.qdrant_collection, " + NORM + " AS h, "
            "         (SELECT count(*) FROM recipe_ingredients ri "
            "          WHERE ri.recipe_id=r.id AND ri.plant_id IS NOT NULL) np, "
            "         (r.chunk_id IS NOT NULL) has_chunk, r.created_at "
            "  FROM recipes r WHERE length(original_text)>=20), "
            "ranked AS ("
            "  SELECT *, count(*) OVER (PARTITION BY h) c, "
            "         first_value(id) OVER (PARTITION BY h "
            "             ORDER BY np DESC, has_chunk DESC, created_at ASC, id ASC) canonical_id, "
            "         row_number() OVER (PARTITION BY h "
            "             ORDER BY np DESC, has_chunk DESC, created_at ASC, id ASC) rn "
            "  FROM grp) "
            "SELECT id, canonical_id, h, book_id, chunk_id, name, category, original_text, "
            "       qdrant_point_id, qdrant_collection "
            "FROM ranked WHERE c>1 AND rn>1"))).all()

        groups = len({r[1] for r in ranked})
        print(f"groups with duplicates: {groups} | rows to delete: {len(ranked)}")
        if DRY:
            for r in ranked[:8]:
                print(f"  del {str(r[0])[:8]} -> keep {str(r[1])[:8]} | {(r[5] or '?')[:50]}")
            print("DRY_RUN — nothing changed.")
            return

        del_ids = [str(r[0]) for r in ranked]
        # snapshot rows + their ingredients into the audit BEFORE deleting
        for r in ranked:
            await db.execute(text(
                "INSERT INTO recipe_dedup_audit (id,canonical_id,fingerprint,book_id,chunk_id,"
                "name,category,original_text,qdrant_point_id,qdrant_collection,ingredients_json) "
                "SELECT :id,:can,:fp,:bk,:ch,:nm,:cat,:ot,:qp,:qc, "
                "  COALESCE((SELECT jsonb_agg(to_jsonb(ri)) FROM recipe_ingredients ri "
                "            WHERE ri.recipe_id=:id), '[]'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"),
                {"id": str(r[0]), "can": str(r[1]), "fp": r[2],
                 "bk": str(r[3]) if r[3] else None, "ch": str(r[4]) if r[4] else None,
                 "nm": r[5], "cat": r[6], "ot": r[7], "qp": r[8], "qc": r[9]})
        await db.commit()

        # collect qdrant points to remove, grouped by collection
        by_coll: dict[str, list[str]] = {}
        for r in ranked:
            pid, coll = r[8], (r[9] or "recipes_v2")
            if pid:
                by_coll.setdefault(coll, []).append(pid)

        # delete the duplicate rows (recipe_ingredients cascade)
        CH = 500
        for i in range(0, len(del_ids), CH):
            chunk = del_ids[i:i + CH]
            await db.execute(text("DELETE FROM recipes WHERE id = ANY(CAST(:ids AS uuid[]))"),
                             {"ids": chunk})
        await db.commit()

        # remove the orphaned qdrant points
        removed = 0
        for coll, pids in by_coll.items():
            for i in range(0, len(pids), CH):
                try:
                    await qdrant.delete_points(coll, pids[i:i + CH])
                    removed += len(pids[i:i + CH])
                except Exception as e:  # noqa: BLE001 — qdrant cleanup is best-effort
                    print(f"  qdrant delete failed ({coll}): {e}")

        left = (await db.execute(text(
            "WITH g AS (SELECT " + NORM + " h FROM recipes WHERE length(original_text)>=20 "
            "GROUP BY 1 HAVING count(*)>1) SELECT count(*) FROM g"))).scalar()
        total = (await db.execute(text("SELECT count(*) FROM recipes"))).scalar()
        print(f"deleted {len(del_ids)} rows | qdrant points removed {removed} | "
              f"dup groups remaining {left} | recipes now {total}")
        print("Audit snapshot in recipe_dedup_audit (reversible).")


if __name__ == "__main__":
    asyncio.run(main())
