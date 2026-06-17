"""Apply recipe.is_monograph verdicts in BATCHES (commit every 25) so no single
giant transaction is held open across hundreds of qdrant-HTTP deletes (which made
the one-shot /quality/bulk-apply time out + roll back). Deletes real→monograph
dumps, dismisses false_positive→real recipes. ≥0.9 confidence only.

Run detached: docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/recipe_mono_apply.py > /tmp/recipe_apply.log 2>&1'
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models.data_quality import DataQualityFinding
from app.routers.quality import _apply_fix_action

CHECK = "domain.recipe_is_monograph"


async def _ids(verdict):
    async with async_session() as db:
        return (await db.execute(select(DataQualityFinding.id).where(
            DataQualityFinding.check_id == CHECK,
            DataQualityFinding.status == "open",
            DataQualityFinding.llm_verdict == verdict,
            DataQualityFinding.llm_confidence >= 0.9))).scalars().all()


async def main():
    real_ids = await _ids("real")
    fp_ids = await _ids("false_positive")
    print(f"delete {len(real_ids)}, dismiss {len(fp_ids)}", flush=True)
    now = datetime.now(timezone.utc)

    B, done, skipped = 25, 0, 0
    for i in range(0, len(real_ids), B):
        batch = real_ids[i:i + B]
        async with async_session() as db:
            for fid in batch:
                f = (await db.execute(select(DataQualityFinding).where(
                    DataQualityFinding.id == fid))).scalar_one()
                try:
                    await _apply_fix_action(db, f)
                    f.status = "fixed"
                    f.resolved_by = "manual-batch"
                    f.resolved_at = now
                except Exception as e:
                    skipped += 1
                    print(f"  skip {fid}: {str(e)[:100]}", flush=True)
            await db.commit()
        done += len(batch)
        print(f"deleted {done}/{len(real_ids)} (skipped {skipped})", flush=True)

    async with async_session() as db:
        for fid in fp_ids:
            f = (await db.execute(select(DataQualityFinding).where(
                DataQualityFinding.id == fid))).scalar_one()
            f.status = "dismissed"
            f.resolved_by = "manual-batch"
            f.resolved_at = now
        await db.commit()
    print(f"DONE: deleted {done - skipped}, dismissed {len(fp_ids)}, skipped {skipped}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
