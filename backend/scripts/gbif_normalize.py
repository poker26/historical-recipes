# -*- coding: utf-8 -*-
"""Normalize corpus card latins to the GBIF ACCEPTED name — the sanctioned (authoritative) path.

The Cherepanov OCR spine could NOT do this safely — its synonym→accepted links are OCR-mis-parsed
(~25%): it mapped «Eurotia ceratoides» to «Krascheninnikovia pungens» (WRONG epithet). GBIF is the
external truth (memory: «identity mutations → only GBIF/POWO»): it maps it to «Krascheninnikovia
ceratoides» (right). For every corpus card GBIF flags status=SYNONYM, this rewrites name_latin to
the accepted name. A pure field update (no identity merge), grounded in GBIF, reversible (audit
`gbif_latin_norm_audit`); the old latin is kept as a searchable alias.

Resolution reuses the cached `gbif_taxon_cache.usage_key`: GET /species/{usage_key} returns the
`accepted` name string directly. Cached in a new `accepted_canonical` column (resumable).

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/gbif_normalize.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import os

import httpx
from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant
from app.services.plant_matching import _latin_key

APPLY = bool(os.environ.get("APPLY"))
SPECIES_URL = "https://api.gbif.org/v1/species/{}"


async def _accepted_for(client, usage_key):
    try:
        r = await client.get(SPECIES_URL.format(usage_key), timeout=30)
        return (r.json() or {}).get("accepted")
    except Exception:
        return None


async def main():
    async with async_session() as db:
        await db.execute(text("ALTER TABLE gbif_taxon_cache ADD COLUMN IF NOT EXISTS accepted_canonical text"))
        await db.commit()
        # corpus latin keys → card(s)
        plants = (await db.execute(select(Plant))).scalars().all()
        key2cards: dict[str, list] = {}
        for p in plants:
            k = _latin_key(p.name_latin)
            if k:
                key2cards.setdefault(k, []).append(p)
        # cached SYNONYM keys present in the corpus, with their usage_key + any cached accepted
        rows = (await db.execute(text(
            "SELECT latin_key, usage_key, accepted_canonical FROM gbif_taxon_cache "
            "WHERE status='SYNONYM' AND latin_key = ANY(:k)"), {"k": list(key2cards)})).all()
        need = [(k, uk) for k, uk, ac in rows if uk and not ac]
        print(f"corpus SYNONYM cards: {len(rows)} | need accepted-name fetch: {len(need)}")

    # fetch accepted names (rate-limited, resumable)
    if need:
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient() as client:
            async def fill(k, uk):
                async with sem:
                    acc = await _accepted_for(client, uk)
                    if acc:
                        async with async_session() as db:
                            await db.execute(text(
                                "UPDATE gbif_taxon_cache SET accepted_canonical=:a WHERE latin_key=:k"),
                                {"a": acc, "k": k})
                            await db.commit()
            await asyncio.gather(*(fill(k, uk) for k, uk in need))

    # build normalization plan — GUARDED against corpus latin noise (the Acer L.→Hydnellum
    # fungus disaster): only EXACT GBIF matches, high confidence, and a kingdom that's consistent
    # with the card; never a genus-rank card (a genus must not become a species).
    _KMAP = {"растение": "Plantae", "гриб": "Fungi"}
    async with async_session() as db:
        acc_map = {r[0]: (r[1], r[2], r[3]) for r in (await db.execute(text(
            "SELECT latin_key, accepted_canonical, kingdom, confidence FROM gbif_taxon_cache "
            "WHERE status='SYNONYM' AND accepted_canonical IS NOT NULL "
            "AND match_type='EXACT' AND COALESCE(confidence,0) >= 95"))).all()}
    updates = []
    skipped_guard = 0
    for k, (acc, gk, conf) in acc_map.items():
        if _latin_key(acc) == k:            # accepted == own (no real change)
            continue
        for p in key2cards.get(k, []):
            if (getattr(p, "rank", None) or "species") == "genus":
                skipped_guard += 1; continue
            want = _KMAP.get(p.kingdom or "растение")     # NULL kingdom → assume Plantae
            if gk and want and gk != want:                # cross-kingdom match = bad input
                skipped_guard += 1; continue
            updates.append((p, acc))

    print(f"\nlatin normalizations (EXACT synonym → GBIF accepted): {len(updates)} "
          f"| skipped by guard (genus/cross-kingdom): {skipped_guard}")
    for p, acc in updates[:14]:
        print(f"   «{(p.name or '?')[:22]:22}» {(p.name_latin or '')[:26]:26} -> {acc[:32]}")

    if not APPLY:
        print("\nDRY — nothing changed. Set APPLY=1.")
        return

    async with async_session() as db:
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS gbif_latin_norm_audit (plant_id uuid, old_latin text, "
            "new_latin text, at timestamptz DEFAULT now())"))
        for p, acc in updates:
            obj = (await db.execute(select(Plant).where(Plant.id == p.id))).scalar_one()
            await db.execute(text(
                "INSERT INTO gbif_latin_norm_audit (plant_id, old_latin, new_latin) VALUES (:i,:o,:n)"),
                {"i": str(obj.id), "o": obj.name_latin, "n": acc})
            hist = list(obj.names_historical or [])
            if obj.name_latin and obj.name_latin not in hist:
                hist.append(obj.name_latin)
            obj.names_historical = hist or None
            obj.name_latin = acc
        await db.commit()
    print(f"\nnormalized {len(updates)} card latins to GBIF accepted. audit: gbif_latin_norm_audit.")


if __name__ == "__main__":
    asyncio.run(main())
