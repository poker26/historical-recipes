# -*- coding: utf-8 -*-
"""Purge the 366 unidentifiable OCR-garbage plant cards: name_latin contains
Cyrillic AND the NAME is also broken (catalog list-marker like «24.», a single-
letter genus abbreviation «Я.», OCR-junk chars, or stray Latin letters). These
have no recoverable identity (neither name nor latin). Facts cascade via FK,
recipe links SET NULL (recipes preserved, just unlinked), plants_v2 qdrant points
dropped. The book full_text stays, so these are recoverable via a later reparse.

Writes the deleted id|name|latin list to /tmp/purged_cyrillic_latin.txt for the
record before deleting.
"""
import asyncio

from sqlalchemy import delete, text

from app.database import async_session
from app.models.plant import Plant
from app.services import qdrant as qdrant_svc

# Must match EXACTLY the filter that counted 366.
FILTER = r"""
    name_latin ~ '[А-Яа-яЁё]'
    AND (
        name ~ '^[0-9]'
        OR name ~ '^[A-ZА-ЯЁ]\.'
        OR name ~ '[@{}\[\]\$<>|]'
        OR name ~ '[A-Za-z]'
    )
"""


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(
            f"SELECT id, name, coalesce(name_latin,'') FROM plants WHERE {FILTER} ORDER BY name"
        ))).all()
        ids = [str(r[0]) for r in rows]
        print(f"matched {len(ids)} cards")

        # Audit record before destruction.
        with open("/tmp/purged_cyrillic_latin.txt", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{r[0]}\t{r[1]}\t{r[2]}\n")
        print("wrote audit list -> /tmp/purged_cyrillic_latin.txt")

        if not ids:
            return

        # Drop qdrant points (best-effort, in chunks).
        for i in range(0, len(ids), 200):
            try:
                await qdrant_svc.delete_points("plants_v2", ids[i:i + 200])
            except Exception as e:
                print("qdrant chunk failed (continuing):", str(e)[:80])

        res = await s.execute(delete(Plant).where(Plant.id.in_(ids)))
        await s.commit()
        print(f"deleted {res.rowcount} plant cards (facts cascaded, recipe links SET NULL, qdrant purged)")


if __name__ == "__main__":
    asyncio.run(main())
