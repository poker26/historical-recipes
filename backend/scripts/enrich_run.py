"""One-shot enrichment runner for the freshly latin-tagged clean-latin cards.
Loops enrich_plants_inat (iNat → Russian common name → promote into bare-latin
name) until the unsynced floor drains. Paced 2s/req to leave the live walk iNat
headroom; breaks on a pure-throttle batch instead of hammering.
"""
import asyncio

from app.database import async_session
from app.services.inaturalist import enrich_plants_inat


async def main():
    resolved = names = promoted = batches = 0
    while True:
        async with async_session() as db:
            r = await enrich_plants_inat(db, dry_run=False, limit=150, pace_seconds=2.0)
        batches += 1
        resolved += r.get("taxa_resolved", 0)
        names += r.get("names_set", 0)
        promoted += r.get("names_promoted", 0)
        print(f"batch {batches}: resolved={r.get('taxa_resolved')} names={r.get('names_set')} "
              f"promoted={r.get('names_promoted')} remaining={r.get('remaining')} "
              f"throttled={r.get('throttled')}", flush=True)
        if not r.get("processed"):
            break
        if (r.get("taxa_resolved", 0) == 0 and r.get("no_match", 0) == 0
                and r.get("throttled", 0) > 0):
            print("pure-throttle batch — stopping", flush=True)
            break
    print(f"DONE: resolved={resolved} names_set={names} promoted_to_name={promoted}", flush=True)


asyncio.run(main())
