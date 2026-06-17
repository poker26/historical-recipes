"""Sequentially OCR the remaining wishlist pilot books (one at a time), in
value order, so they're ready for their extract pilots. Reuses the hardened
per-step wizard endpoints (classify → extract, per-page commit + resume). Stops
at status `extracted` per book — nothing runs extraction (dispatcher is down).

Run detached:
  docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/ocr_wishlist.py > /tmp/ocr_wl.log 2>&1'
"""
import asyncio
import httpx
from sqlalchemy import select, func

from app.database import async_session
from app.models.book import Book, BookPage

BASE = "http://localhost:8000/api/wizard"
DONE_STATES = {"extracted", "plants_extracted", "recipes_extracted",
               "compounds_extracted", "indexed", "done", "published"}

# Priority order: untested-shape pilots first, lower-fit last. Excludes the junk
# determiner (Гроссгейм/Кавказ) and the broken Соколов-России (no file bytes).
TARGETS = [
    ("f8374a36-05cf-4e63-b839-8b8b1339ed29", "Гусынин — токсикология"),
    ("e9d4df3a-15ac-44a9-af21-23e53393e39e", "Орехов — химия алкалоидов"),
    ("4eb2afac-d9a6-4bcd-8bc7-1dd3e833e43a", "Племенков — химия природ. соединений"),
    ("00b1c4a2-89ec-48f8-9736-d808e7af9cbf", "ГФ РФ т.IV — ЛРС"),
    ("0e4cc512-af2e-458c-afc4-58b93fbeed75", "Войткевич — эфирные масла"),
    ("876319eb-0ba3-40c2-906a-bfec7922dac2", "Соколов — Фитотерапия"),
    ("140d5ce9-66ce-46b5-9d59-ec5d8805f2df", "Машковский — лекарств. средства"),
]


async def _status(bid):
    async with async_session() as db:
        b = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        pages = (await db.execute(
            select(func.count()).select_from(BookPage).where(BookPage.book_id == bid))).scalar()
        return b.status, pages


async def main():
    print(f"OCR queue: {len(TARGETS)} book(s)", flush=True)
    async with httpx.AsyncClient(timeout=300) as c:
        for bid, title in TARGETS:
            st, _ = await _status(bid)
            if st in DONE_STATES:
                print(f"SKIP (already {st}): {title}", flush=True)
                continue
            print(f"\n>>> START {title}", flush=True)
            try:
                r = await c.post(f"{BASE}/{bid}/classify")
                print(f"  classify: {r.status_code} {r.text[:150]}", flush=True)
                r = await c.post(f"{BASE}/{bid}/extract")
                print(f"  extract: {r.status_code} {r.text[:80]}", flush=True)
            except Exception as e:
                print(f"  trigger failed: {e}", flush=True)
                continue
            for _ in range(300):                     # cap ~2.5h/book
                await asyncio.sleep(30)
                st, pages = await _status(bid)
                if st in DONE_STATES:
                    print(f"  DONE {title}: {st}, {pages} pages", flush=True)
                    break
            else:
                print(f"  TIMEOUT {title} (status={st}, {pages} pages) — moving on", flush=True)
    print("\nALL WISHLIST OCR PROCESSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
