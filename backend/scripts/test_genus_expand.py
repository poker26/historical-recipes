"""LLM-free test of expand_genus_abbreviations on a book's OCR'd pages."""
import asyncio
import sys
import re

from sqlalchemy import select

from app.database import async_session
from app.models.book import BookPage
from app.services.postprocessor import clean_ocr_text
from app.services.refbook_preprocess import expand_genus_abbreviations


async def main():
    book_id = sys.argv[1]
    async with async_session() as db:
        texts = (await db.execute(
            select(BookPage.raw_text).where(BookPage.book_id == book_id)
            .order_by(BookPage.page_number))).scalars().all()
    raw = clean_ocr_text("\n\n".join(t for t in texts if t))
    new, stats = expand_genus_abbreviations(raw)
    print(f"headers={stats['headers']}  expansions={stats['expansions']}  "
          f"genera({len(stats['genera'])})={stats['genera'][:25]}", flush=True)

    print("\n=== genus headers detected (first 20) ===", flush=True)
    for ln in raw.split("\n"):
        if re.search(r"\bРод\s+\d+\b", ln):
            print("  " + ln.strip()[:90], flush=True)
            if (i := getattr(main, "_h", 0)) >= 19:
                break
            main._h = i + 1

    print("\n=== sample BEFORE → AFTER on species lines ('— Ш.' etc.) ===", flush=True)
    shown = 0
    rl = raw.split("\n")
    nl = new.split("\n")
    for a, b in zip(rl, nl):
        if a != b and shown < 25:
            print(f"  - {a.strip()[:80]}", flush=True)
            print(f"  + {b.strip()[:80]}", flush=True)
            shown += 1


if __name__ == "__main__":
    asyncio.run(main())
