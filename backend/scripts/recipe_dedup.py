"""Deduplicate recipes that are byte-identical in BOTH name AND original_text
(safe set: 483 groups / 545 excess). Same-book groups are pure extractor
artifacts; cross-book groups are real provenance (one recipe in 2+ books) — for
those the merged book_ids are written to a ProcessingLog audit row before the
losers are deleted, so provenance is recoverable.

Survivor = the copy whose ingredients carry a plant_id link (most useful), else
the earliest (min created_at, then id). Losers + their qdrant points are deleted,
batched (commit per group) so no giant transaction is held open.

NOT touched: text-identical-but-different-name recipes — those are legit
preparation splits (настойка vs настой from the same source passage).

Usage: python scripts/recipe_dedup.py [apply]   (default = dry-run)
"""
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select, func, delete

from app.database import async_session
from app.models.recipe import Recipe, RecipeIngredient
from app.models.book import ProcessingLog
from app.services import qdrant as qdrant_svc

APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"


def _key(r):
    import re
    return (r.name or "").lower() + "|" + re.sub(r"\s+", "", (r.original_text or "").lower())


async def main():
    async with async_session() as db:
        recs = (await db.execute(select(
            Recipe.id, Recipe.name, Recipe.original_text, Recipe.book_id,
            Recipe.created_at, Recipe.qdrant_point_id, Recipe.qdrant_collection
        ).where(func.length(Recipe.original_text) > 20))).all()
        linked = set((await db.execute(
            select(RecipeIngredient.recipe_id).where(RecipeIngredient.plant_id.isnot(None))
        )).scalars().all())

    groups: dict[str, list] = {}
    for r in recs:
        groups.setdefault(_key(r), []).append(r)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    total_excess = sum(len(v) - 1 for v in dup_groups.values())
    multibook = sum(1 for v in dup_groups.values() if len({r.book_id for r in v}) > 1)
    print(f"{'APPLY' if APPLY else 'DRY-RUN'}: {len(dup_groups)} groups, {total_excess} excess "
          f"copies ({multibook} cross-book)", flush=True)

    def _rank(r):
        return (0 if r.id in linked else 1, r.created_at or datetime.max.replace(tzinfo=timezone.utc), str(r.id))

    sample_shown = 0
    now = datetime.now(timezone.utc)
    deleted = 0
    for k, v in dup_groups.items():
        v_sorted = sorted(v, key=_rank)
        survivor, losers = v_sorted[0], v_sorted[1:]
        books = sorted({str(r.book_id) for r in v if r.book_id})
        if not APPLY:
            if sample_shown < 8:
                print(f"  «{survivor.name}» keep {str(survivor.id)[:8]} "
                      f"drop {len(losers)} (books={len(books)})", flush=True)
                sample_shown += 1
            continue
        async with async_session() as db:
            if len(books) > 1:
                db.add(ProcessingLog(book_id=survivor.book_id, step="recipe_dedup",
                                     status="completed",
                                     details={"survivor": str(survivor.id), "name": survivor.name,
                                              "merged_book_ids": books,
                                              "dropped": [str(r.id) for r in losers]}))
            for r in losers:
                if r.qdrant_point_id and r.qdrant_collection:
                    try:
                        await qdrant_svc.delete_points(r.qdrant_collection, [r.qdrant_point_id])
                    except Exception:
                        pass
                await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == r.id))
                await db.execute(delete(Recipe).where(Recipe.id == r.id))
            await db.commit()
        deleted += len(losers)
        if deleted % 100 < len(losers):
            print(f"  deleted {deleted}/{total_excess}", flush=True)

    print(f"DONE ({'applied' if APPLY else 'dry-run'}): would delete {total_excess}" if not APPLY
          else f"DONE: deleted {deleted}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
