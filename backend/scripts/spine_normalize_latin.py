# -*- coding: utf-8 -*-
"""Normalize corpus card latins to the Cherepanov ACCEPTED name — the safe spine payoff.

For every card whose `name_latin` is a SYNONYM in the spine (`_latin_key` ∈ taxon_synonym →
accepted_key ∈ taxon_backbone), rewrite name_latin to the accepted «Genus species». Unlike a
merge this is a pure field update — no identity consolidation, so a wrong corpus latin can't
mis-merge two real cards; the worst case is a synonym→accepted rewrite of an already-wrong latin
(no worse than before). Grounded in Cherepanov, reversible (audit `spine_latin_norm_audit`). The
old latin is kept as a searchable alias. Catches cross-genus synonyms no other tool resolves
(Lepidium repens→Cardaria repens, Eurotia ceratoides→Krascheninnikovia pungens).

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/spine_normalize_latin.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import os

from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant
from app.services.plant_matching import _latin_key

APPLY = bool(os.environ.get("APPLY"))


async def main():
    async with async_session() as db:
        acc = {r[0] for r in (await db.execute(text(
            "SELECT DISTINCT accepted_key FROM taxon_backbone WHERE accepted_key IS NOT NULL"))).all()}
        syn = {r[0]: r[1] for r in (await db.execute(text(
            "SELECT syn_key, accepted_key FROM taxon_synonym "
            "WHERE syn_key IS NOT NULL AND accepted_key IS NOT NULL"))).all()}
        # accepted_key → canonical «Genus species» (+ author) from the backbone
        bb = {}
        for k, g, s, a in (await db.execute(text(
                "SELECT accepted_key, genus, species, author FROM taxon_backbone"))).all():
            bb.setdefault(k, (g, s, a))

        plants = (await db.execute(select(Plant))).scalars().all()
        updates = []
        for p in plants:
            k = _latin_key(p.name_latin)
            if not k or k in acc:
                continue
            ak = syn.get(k)
            if ak not in acc or ak == k:
                continue
            g, s, a = bb[ak]
            new_latin = f"{g} {s}" + (f" {a}" if a else "")
            if _latin_key(new_latin) == k:        # no real change (same key) — skip
                continue
            updates.append((p, new_latin))

        print(f"cards with a synonym latin → normalize to accepted: {len(updates)}")
        for p, nl in updates[:12]:
            print(f"   «{(p.name or '?')[:22]:22}» {(p.name_latin or '')[:28]:28} -> {nl[:30]}")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS spine_latin_norm_audit (plant_id uuid, old_latin text, "
            "new_latin text, at timestamptz DEFAULT now())"))
        for p, nl in updates:
            await db.execute(text(
                "INSERT INTO spine_latin_norm_audit (plant_id, old_latin, new_latin) VALUES (:i,:o,:n)"),
                {"i": str(p.id), "o": p.name_latin, "n": nl})
            hist = list(p.names_historical or [])
            if p.name_latin and p.name_latin not in hist:
                hist.append(p.name_latin)          # keep the old (synonym) latin searchable
            p.names_historical = hist or None
            p.name_latin = nl
        await db.commit()
        print(f"\nnormalized {len(updates)} card latins to the Cherepanov accepted name. "
              "audit: spine_latin_norm_audit.")


if __name__ == "__main__":
    asyncio.run(main())
