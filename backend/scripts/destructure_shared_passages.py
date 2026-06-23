# -*- coding: utf-8 -*-
"""Run Phase A: distil each shared-passage recipe's specific remedy span into normalized_text.

Groups recipes by identical-normalised original_text with >1 distinct name (the shared-passage
shape, 1206 groups / 3032 rows), runs the extractive LLM per group (concurrency-limited), gates
each span, and writes the survivors to `recipes.normalized_text` (purely additive — the field is
empty for all 53k; indexing/recipe-API/flagship prefer it with original_text fallback).

    DRY_RUN=1 LIMIT=4 docker compose exec -T -e PYTHONPATH=/app -e DRY_RUN=1 -e LIMIT=4 backend python scripts/destructure_shared_passages.py
                     docker compose exec -T -e PYTHONPATH=/app backend python scripts/destructure_shared_passages.py
"""
import asyncio
import os
from collections import OrderedDict

from sqlalchemy import text

from app.database import async_session
from app.services.recipe_destructure import destructure_passage, grounded

DRY = bool(os.environ.get("DRY_RUN"))
LIMIT = int(os.environ.get("LIMIT", "0"))      # 0 = all groups
CONC = int(os.environ.get("CONC", "5"))
NORM = r"btrim(lower(regexp_replace(original_text, '\s+', ' ', 'g')))"


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT " + NORM + " h, id::text, name, original_text "
            "FROM recipes WHERE length(original_text)>=80 AND " + NORM + " IN ("
            "  SELECT " + NORM + " FROM recipes WHERE length(original_text)>=80 "
            "  GROUP BY 1 HAVING count(*)>1 AND count(DISTINCT name)>1) "
            "ORDER BY h, id"))).all()
    groups: "OrderedDict[str, list]" = OrderedDict()
    passage_of: dict[str, str] = {}
    for h, rid, name, ot in rows:
        groups.setdefault(h, []).append((rid, name))
        passage_of.setdefault(h, ot)               # any member (identical-normalised)
    hs = list(groups)
    if LIMIT:
        hs = hs[:LIMIT]
    print(f"groups: {len(groups)} | running: {len(hs)} | rows in run: {sum(len(groups[h]) for h in hs)}")

    sem = asyncio.Semaphore(CONC)

    async def do(h):
        async with sem:
            try:
                return h, await destructure_passage(passage_of[h], groups[h])
            except Exception as e:  # noqa: BLE001
                print(f"  group err: {type(e).__name__}: {e}")
                return h, {}

    done = await asyncio.gather(*(do(h) for h in hs))

    updates: dict[str, str] = {}
    got = 0
    for h, res in done:
        for rid, span in (res or {}).items():
            if span:
                got += 1
                updates[rid] = span

    if DRY:
        for h, res in done[:4]:
            print("\n=== passage:", passage_of[h][:140].replace("\n", " "), "…")
            for rid, name in groups[h]:
                sp = (res or {}).get(rid)
                print(f"  · {name[:42]:42} -> {('[span] ' + sp[:90]) if sp else '∅ (keeps original)'}")
        print(f"\nDRY_RUN — would set normalized_text on {got} rows. Nothing changed.")
        return

    async with async_session() as db:
        for rid, span in updates.items():
            await db.execute(text("UPDATE recipes SET normalized_text=:t WHERE id=:id"),
                             {"t": span, "id": rid})
        await db.commit()
    print(f"set normalized_text on {got} / {sum(len(groups[h]) for h in hs)} rows.")


if __name__ == "__main__":
    asyncio.run(main())
