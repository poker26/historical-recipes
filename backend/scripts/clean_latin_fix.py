"""Mechanical fix for clean-latin-named cards (Coronilla juncea, Calendula): the
latin is sitting in `name`. Parse it into name_latin (overwriting abbreviated/
authored forms like «C. juncea L.»), reset inat_synced_at so the enrichment pass
resolves it → Russian common name → promotes that into `name` (bare-latin headword
rule). Cards iNat has no Russian name for keep a clean latin name (fine to show).
"""
import asyncio
import re

from sqlalchemy import text

from app.database import async_session

JUNK = re.compile(r"[\$@{}\[\]|<>0-9!]")
CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")
CLEAN_BINO = re.compile(r"^[A-Z][a-z]{2,}\s+[a-z]{2,}")
CLEAN_GENUS = re.compile(r"^[A-Z][a-z]{2,}\.?$")
ABBREV = re.compile(r"^[А-ЯA-Z]\.\s*\S")


def name_bucket(name):
    n = (name or "").strip()
    if JUNK.search(n):
        return "ocr_junk"
    if ABBREV.match(n):
        return "abbrev_genus"
    if not CYR.search(n) and CLEAN_BINO.match(n):
        return "clean_latin_binomial"
    if not CYR.search(n) and CLEAN_GENUS.match(n):
        return "clean_latin_genus"
    if CYR.search(n) and LAT.search(n):
        return "mixed_script"
    return "other_messy"


def parse_latin(name):
    toks = re.findall(r"[A-Za-z]+", name or "")
    if not toks:
        return None
    g = toks[0].capitalize()
    if len(toks) >= 2 and toks[1].islower() and len(toks[1]) >= 2:
        return f"{g} {toks[1].lower()}"
    return g


async def main():
    async with async_session() as s:
        rows = (await s.execute(text(r"""
            SELECT id, name FROM plants
            WHERE name ~ '[A-Za-z]' OR name ~ '[\$@{}\[\]|<>]' OR name ~ '^[А-ЯA-Z]\.'
        """))).all()
    fix = []
    for pid, name in rows:
        if name_bucket(name) in ("clean_latin_binomial", "clean_latin_genus"):
            lat = parse_latin(name)
            if lat:
                fix.append((str(pid), lat))
    print(f"clean-latin cards to fix: {len(fix)}")
    print("samples:", [(f[1]) for f in fix[:8]])

    done = 0
    for i in range(0, len(fix), 200):
        chunk = fix[i:i + 200]
        async with async_session() as s:
            for pid, lat in chunk:
                await s.execute(text(
                    "UPDATE plants SET name_latin=:l, inat_synced_at=NULL WHERE id=CAST(:i AS uuid)"),
                    {"l": lat, "i": pid})
            await s.commit()
        done += len(chunk)
        print(f"  name_latin set {done}/{len(fix)}", flush=True)
    print("SETUP DONE — cards are now latin-tagged + unsynced; run enrichment next.")


asyncio.run(main())
