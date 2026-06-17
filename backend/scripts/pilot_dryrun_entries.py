"""Pilot DRY-RUN: extract plant entries from a few chunks of a book and report,
per extracted plant, whether it would ENRICH an existing card (and which) or
CREATE A NEW one — WITHOUT writing anything to the DB.

Mirrors the read path of `_resolve_plant` (latin-exact → latin-key → name-exact →
unambiguous-fuzzy → new) so the verdict matches what the real ingest would do.

Usage (inside backend container):
    python scripts/pilot_dryrun_entries.py <book_id> [chunk_start] [chunk_count]
"""
import asyncio
import re
import sys

from sqlalchemy import select, func

from app.database import async_session
from app.models.plant import Plant
from app.models.book import Book, BookPage
from app.services.plant_extractor import _split_into_chunks, _extract_single
from app.services.plant_matching import same_plant_identity, _latin_key
from app.services.postprocessor import clean_ocr_text
from app.services.refbook_preprocess import expand_genus_abbreviations

_ABBR_GENUS_RE = re.compile(r"^[А-ЯЁ]\.\s")


def _norm(s):
    return (s or "").strip().lower()


async def _classify(db, all_plants, ep):
    """Read-only mirror of the PLANNED _resolve_plant (with the pilot fixes).

    Adds two safety rules over the current resolver: (1) only trust name_latin
    that is a real binomial (`_latin_key` non-None) — OCR-garbled latin must not
    match or fill; (2) never fuzzy-match a name still carrying an unexpanded
    single-letter genus ("Ш. xxx") — a visible stub beats a wrong merge.
    """
    name = getattr(ep, "name", None)
    latin = getattr(ep, "name_latin", None)
    if latin and _latin_key(latin) is None:
        latin = None  # OCR junk — don't use it for identity

    if latin:
        p = (await db.execute(
            select(Plant).where(func.lower(Plant.name_latin) == _norm(latin)))).scalars().first()
        if p:
            return "ENRICH", p, "latin_exact"
        key = _latin_key(latin)
        cands = [p for p in all_plants if _latin_key(p.name_latin) == key]
        if cands:
            return "ENRICH", max(cands, key=lambda p: len(p.name_latin or "")), "latin_key"
    if name:
        p = (await db.execute(
            select(Plant).where(func.lower(Plant.name) == _norm(name)))).scalars().first()
        if p:
            return "ENRICH", p, "name_exact"
    if name and _ABBR_GENUS_RE.match(name.strip()):
        return "NEW", None, "unexpanded_genus(skip_fuzzy)"
    if name:
        cands = [p for p in all_plants if same_plant_identity(name, p.name)]
        if len(cands) == 1:
            return "ENRICH", cands[0], "fuzzy"
        if len(cands) > 1:
            return "NEW", None, f"AMBIGUOUS_fuzzy({len(cands)})"
    return "NEW", None, "no_match"


async def main():
    book_id = sys.argv[1]
    chunk_start = int(sys.argv[2]) if len(sys.argv) > 2 else None
    chunk_count = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
        title = book.title
        # Assemble from the pages OCR'd SO FAR (works mid-OCR — doesn't wait for the
        # whole book). Rule-based clean only (instant, no LLM); good enough to
        # validate the parser + matching on this structurally-homogeneous book.
        page_texts = (await db.execute(
            select(BookPage.raw_text).where(BookPage.book_id == book_id)
            .order_by(BookPage.page_number))).scalars().all()
    raw = clean_ocr_text("\n\n".join(t for t in page_texts if t))
    full_text, gstats = expand_genus_abbreviations(raw)
    print(f"genus-expand: headers={gstats['headers']} expansions={gstats['expansions']}", flush=True)
    chunks = _split_into_chunks(full_text)
    n = len(chunks)
    print(f"BOOK: {title}\npages OCR'd so far: {len(page_texts)} -> {len(full_text)} chars "
          f"-> {n} chunks (rule-clean only)", flush=True)
    if not n:
        print("NO TEXT — OCR hasn't committed pages yet", flush=True)
        return

    # Default: sample the LAST chunk_count chunks of what's OCR'd — most likely past
    # the front matter (intro/abbreviations) and into real species entries.
    if chunk_start is None:
        chunk_start = max(0, n - chunk_count)
    picked = list(range(chunk_start, min(chunk_start + chunk_count, n)))
    print(f"Sampling chunks {picked} of {n}\n" + "=" * 60, flush=True)

    async with async_session() as db:
        all_plants = (await db.execute(select(Plant))).scalars().all()
    print(f"(base has {len(all_plants)} plant cards)\n", flush=True)

    tally = {"ENRICH": 0, "NEW": 0}
    for ci in picked:
        chunk = chunks[ci]
        print(f"\n---- CHUNK {ci} ({len(chunk)} chars) ----", flush=True)
        print("  TEXT HEAD: " + repr(chunk[:500]), flush=True)
        try:
            plants = await _extract_single(chunk, title)
        except Exception as e:
            print(f"  extract failed: {e}", flush=True)
            continue
        print(f"  extracted {len(plants)} plant(s)", flush=True)
        async with async_session() as db:
            for ep in plants:
                verdict, mp, via = await _classify(db, all_plants, ep)
                tally[verdict] = tally.get(verdict, 0) + 1
                ncomp = len(getattr(ep, "compounds", []) or [])
                nuse = len(getattr(ep, "medicinal_uses", []) or [])
                comps = ", ".join((getattr(c, "compound", "") or "")
                                  for c in (getattr(ep, "compounds", []) or [])[:6])
                print(f"\n  • «{getattr(ep,'name','?')}»  lat='{getattr(ep,'name_latin','') or '-'}'"
                      f"  comps={ncomp} uses={nuse}", flush=True)
                print(f"    → {verdict} via {via}", flush=True)
                if mp is not None:
                    print(f"      matched card: «{mp.name}»  lat='{mp.name_latin or '-'}'  id={mp.id}", flush=True)
                if comps:
                    print(f"      состав sample: {comps}", flush=True)
    print("\n" + "=" * 60, flush=True)
    print(f"SUMMARY: ENRICH={tally.get('ENRICH',0)}  NEW={tally.get('NEW',0)}  "
          f"(NEW = either genuinely missing species or a dup we'd create)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
