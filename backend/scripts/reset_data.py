"""Wipe processing data so you can re-debug the pipeline on a clean slate.

Run INSIDE the backend container (it has DB / Qdrant / MinIO config):

    # Full wipe — removes books, uploaded PDFs, all derived data, Qdrant vectors:
    docker compose exec backend python scripts/reset_data.py --all --yes

    # Keep books + their PDFs + full_text, but clear all derived data
    # (recipes, sections, chunks, the global ingredient dictionary, Qdrant) and
    # rewind each book's wizard to the "analyze" step:
    docker compose exec backend python scripts/reset_data.py --keep-books --yes

Without --yes it prints what it WOULD delete and exits (dry run).
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.database import async_session
from app.services import minio as minio_svc
from app.services import qdrant as qdrant_svc

QDRANT_COLLECTION = "recipes_v2"

# Derived data — always cleared. Order doesn't matter (TRUNCATE ... CASCADE).
DERIVED_TABLES = [
    "recipe_ingredients",
    "recipes",
    "book_sections",
    "book_chunks",
    "ingredient_synonyms",
    "ingredients",
]
# Also cleared on --keep-books (per-book artifacts that the wizard regenerates).
KEEP_BOOKS_ALSO = [
    "processing_log",
    "book_pages",
]
# Everything (full wipe).
ALL_TABLES = DERIVED_TABLES + KEEP_BOOKS_ALSO + ["books"]


async def _counts(db, tables: list[str]) -> dict[str, int]:
    out = {}
    for t in tables:
        out[t] = await db.scalar(text(f"SELECT count(*) FROM {t}"))
    return out


async def reset(keep_books: bool, do_it: bool) -> None:
    async with async_session() as db:
        before = await _counts(db, ALL_TABLES)
        print("Current row counts:")
        for t, n in before.items():
            print(f"  {t:24s} {n}")

        if not do_it:
            print("\nDRY RUN — pass --yes to actually delete. Would clear:")
            tables = (DERIVED_TABLES + KEEP_BOOKS_ALSO) if keep_books else ALL_TABLES
            print("  tables:", ", ".join(tables))
            print(f"  Qdrant collection: {QDRANT_COLLECTION} (recreated)")
            if not keep_books:
                print("  MinIO: all books/* objects")
            else:
                print("  books: kept (PDFs + full_text preserved, wizard rewound to step 4)")
            return

        # 1) MinIO — only on full wipe (keep PDFs when keeping books).
        if not keep_books:
            book_ids = [r[0] for r in (await db.execute(text("SELECT id FROM books"))).all()]
            deleted_files = 0
            for bid in book_ids:
                for f in minio_svc.list_files(f"books/{bid}/"):
                    minio_svc.delete_file(f)
                    deleted_files += 1
            print(f"\nMinIO: deleted {deleted_files} objects")

        # 2) Postgres.
        tables = (DERIVED_TABLES + KEEP_BOOKS_ALSO) if keep_books else ALL_TABLES
        await db.execute(text(
            "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
        ))
        if keep_books:
            # Books survive, but their derived state is gone — rewind the wizard
            # to the analyze step (full_text + pages are kept).
            await db.execute(text(
                "UPDATE books SET wizard_step = 4, status = 'extracted'"
            ))
        await db.commit()
        print(f"Postgres: truncated {len(tables)} tables"
              + (" + rewound books to step 4" if keep_books else ""))

        # 3) Qdrant — drop and let the index step recreate it.
        try:
            await qdrant_svc._request("DELETE", f"/collections/{QDRANT_COLLECTION}")
            print(f"Qdrant: dropped collection {QDRANT_COLLECTION}")
        except Exception as e:
            print(f"Qdrant: drop failed (may not exist yet): {e}")

        after = await _counts(db, ALL_TABLES)
        print("\nAfter:")
        for t, n in after.items():
            print(f"  {t:24s} {n}")
        print("\nDone.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset pipeline data")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="full wipe incl. books + PDFs")
    g.add_argument("--keep-books", action="store_true",
                   help="keep books/PDFs/full_text; clear all derived data")
    ap.add_argument("--yes", action="store_true", help="actually delete (else dry run)")
    args = ap.parse_args()

    asyncio.run(reset(keep_books=args.keep_books, do_it=args.yes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
