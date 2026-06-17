"""Sequentially OCR every remaining «Растительные ресурсы» volume (one at a time).

Reuses the hardened per-step wizard endpoints (classify → extract, per-page commit
+ resume). One volume at a time keeps backend CPU/loop load ≈ a single том1 OCR so
the live site + the alias-adjudication loop stay responsive. Stops at status
`extracted` per book — nothing runs extract_plant_entries (dispatcher is down).

Run detached:
  docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/ocr_rastresursy.py > /tmp/ocr_rr.log 2>&1'
"""
import asyncio
import httpx
from sqlalchemy import select, func

from app.database import async_session
from app.models.book import Book, BookPage

BASE = "http://localhost:8000/api/wizard"
DONE_STATES = {"extracted", "plants_extracted", "recipes_extracted",
               "compounds_extracted", "indexed", "done", "published"}
# Broken/empty upload (no recorded size) — confirmed not needed, skip.
EXCLUDE = {"16e83b8a-fff0-454c-bcfc-0777dde59b14"}


async def _status(bid):
    async with async_session() as db:
        b = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        pages = (await db.execute(
            select(func.count()).select_from(BookPage).where(BookPage.book_id == bid)
        )).scalar()
        return b.status, pages


async def main():
    async with async_session() as db:
        books = (await db.execute(select(Book).where(
            Book.title.ilike("%Растительные ресурсы%"),
            Book.file_path.isnot(None),
        ))).scalars().all()
    targets = [(str(b.id), b.title) for b in books
               if b.status not in DONE_STATES and str(b.id) not in EXCLUDE]
    print(f"to OCR: {len(targets)} volume(s)", flush=True)
    for bid, title in targets:
        print(f"=== {title} ({bid}) ===", flush=True)
    async with httpx.AsyncClient(timeout=300) as c:
        for bid, title in targets:
            print(f"\n>>> START {title}", flush=True)
            try:
                r = await c.post(f"{BASE}/{bid}/classify")
                print(f"  classify: {r.status_code} {r.text[:140]}", flush=True)
                r = await c.post(f"{BASE}/{bid}/extract")
                print(f"  extract: {r.status_code} {r.text[:80]}", flush=True)
            except Exception as e:
                print(f"  trigger failed: {e}", flush=True)
                continue
            for i in range(240):                     # cap ~2h/volume
                await asyncio.sleep(30)
                st, pages = await _status(bid)
                if st in DONE_STATES:
                    print(f"  DONE {title}: {st}, {pages} pages", flush=True)
                    break
            else:
                print(f"  TIMEOUT {title} (status={st}, {pages} pages) — moving on", flush=True)
    print("\nALL VOLUMES PROCESSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
