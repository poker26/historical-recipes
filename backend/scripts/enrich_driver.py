# -*- coding: utf-8 -*-
"""Reliable iNat enrichment driver — bypasses the flaky InatEnrichmentWorkflow
(which kept completing while leaving thousands of unsynced cards untouched). Loops
enrich_plants_inat in bounded batches (each commits as it goes) until no unsynced
latin cards remain. Fills name_modern + photo + inat_taxon_id for our fixed/promoted
cards and the rest of the unenriched tail.
"""
import asyncio

from app.database import async_session
from app.services.inaturalist import enrich_plants_inat


async def main():
    batch = 0
    while True:
        batch += 1
        async with async_session() as db:
            r = await enrich_plants_inat(db, dry_run=False, limit=300)
        print(f"batch {batch}: processed={r.get('processed')} taxa={r.get('taxa_resolved')} "
              f"names={r.get('names_set')} photos={r.get('photos_set')} "
              f"no_match={r.get('no_match')} throttled={r.get('throttled')} remaining={r.get('remaining')}",
              flush=True)
        if not r.get("processed"):
            print("DONE — no more unsynced", flush=True)
            break


if __name__ == "__main__":
    asyncio.run(main())
