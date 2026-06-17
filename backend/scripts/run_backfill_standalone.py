"""Run the canonical latin-backfill (run_backfill_activity) as a standalone
detached process — same fixed gate (iNat-by-ru + LLM + GBIF-kingdom, SQL-exclude
of staged rows), but outside a Temporal worker. heartbeat → no-op.

Run detached: docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/run_backfill_standalone.py > /tmp/backfill.log 2>&1'
"""
import asyncio

from temporalio import activity

# Outside an activity context activity.heartbeat() raises — no-op it.
activity.heartbeat = lambda *a, **k: None

from app.temporal.cleanup_activities import run_backfill_activity  # noqa: E402


async def main():
    res = await run_backfill_activity()
    print("DONE:", res, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
