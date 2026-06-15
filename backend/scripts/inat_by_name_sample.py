# -*- coding: utf-8 -*-
"""PHASE 0 — test iNat as the PRIMARY resolver (writes NOTHING). For each sampled
card, search iNat by the CARD's Russian name (locale=ru) and see what scientific
name it returns, plus iNat's own Russian name and the term it matched on. Verdict
compares the card name to iNat's Russian name/matched_term so we can measure how
much of the 2071 iNat can confidently resolve from the Russian name alone.
"""
import asyncio
import re

from sqlalchemy import text

from app.database import async_session
from app.services.inaturalist import INAT_BASE, _HEADERS
import httpx

SAMPLE = 30

FILTER = r"""
    name_latin ~ '[А-Яа-яЁё]'
    AND NOT (
        name ~ '^[0-9]'
        OR name ~ '^[A-ZА-ЯЁ]\.'
        OR name ~ '[@{}\[\]\$<>|]'
        OR name ~ '[A-Za-z]'
    )
"""

_WORD = re.compile(r"[а-яёa-z]+")


def words(s):
    return _WORD.findall((s or "").lower().replace("ё", "е"))


async def inat_by_ru(client, name):
    qs = [name]
    w = words(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    if w:
        qs.append(w[0])
    for q in qs:
        params = {"q": q, "locale": "ru", "per_page": 5, "is_active": "true"}
        try:
            resp = await client.get(f"{INAT_BASE}/taxa", params=params, headers=_HEADERS)
            if resp.status_code != 200:
                continue
            results = resp.json().get("results", [])
        except Exception:
            continue
        sp = [r for r in results if r.get("rank") == "species"]
        cand = sp[0] if sp else (results[0] if results else None)
        if cand:
            return {"q": q, "sci": cand.get("name"), "ru": cand.get("preferred_common_name"),
                    "matched": cand.get("matched_term"), "rank": cand.get("rank")}
    return None


def verdict(card, ru, matched):
    cw = set(words(card))
    rw = set(words(ru)) | set(words(matched))
    if not cw or not rw:
        return "no_ru"
    shared = cw & rw
    if len(shared) >= 2 or (len(cw) == 1 and shared):
        return "strong"
    if shared:
        return "weak"
    return "miss"


async def process(client, sem, row):
    pid, name, latin = row
    async with sem:
        hit = await inat_by_ru(client, name)
    if not hit:
        return {"name": name, "garbled": latin, "v": "no_hit"}
    v = verdict(name, hit["ru"], hit["matched"]) if hit["rank"] == "species" else "genus_only"
    return {"name": name, "garbled": latin, "sci": hit["sci"], "ru": hit["ru"],
            "matched": hit["matched"], "rank": hit["rank"], "v": v}


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            f"SELECT id, name, name_latin FROM plants WHERE {FILTER} ORDER BY random() LIMIT {SAMPLE}"
        ))).all()
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(*[process(client, sem, r) for r in rows])

    tally = {}
    print("=" * 100)
    for r in results:
        tag = r["v"]
        tally[tag] = tally.get(tag, 0) + 1
        print(f"[{tag:10}] {r['name']}")
        print(f"             garbled : {r['garbled']}")
        if r.get("sci"):
            print(f"             iNat->   : {r['sci']}  | ru={r['ru']} | matched={r['matched']} | rank={r['rank']}")
    print("=" * 100)
    print("VERDICT TALLY:", tally)


if __name__ == "__main__":
    asyncio.run(main())
