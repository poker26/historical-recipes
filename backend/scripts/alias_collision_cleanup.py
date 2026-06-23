# -*- coding: utf-8 -*-
"""Clean the 378 open `alias.collision` findings — the proper foundation for card-identity.

A collision = owner card O carries an alias A (in names_historical) equal to the PRIMARY name of
another card C. Classify by the latin GENUS (first binomial token), which is robust where the
full _latin_key is fooled by abbreviations («A. vernalis») and nomenclatural synonyms:

  * O.genus ≠ C.genus, BOTH full (len>1)  → A is a WRONG alias on O (C's canonical name leaked
                                            onto an unrelated genus) → DROP A from O, and re-route
                                            O's recipes captured via A onto C (the Ferula→«фенхель»
                                            →Фенхель class). HIGH-CONFIDENCE, applied here.
  * O.genus == C.genus                     → same genus: synonym-merge or species folk-overlap →
                                            REVIEW (left untouched).
  * either genus abbreviated / missing     → AMBIGUOUS (left untouched).

Only the different-genus DROPs are auto-applied (with recipe re-route + audit `alias_drop_audit`);
findings for fully-resolved owners are marked resolved.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/alias_collision_cleanup.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import json
import os
import re

from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant

APPLY = bool(os.environ.get("APPLY"))
_NORM = re.compile(r"[^а-яёa-z0-9 ]+", re.I)


def norm(s: str) -> str:
    return _NORM.sub("", (s or "").lower()).strip()


def genus(latin: str | None) -> str | None:
    """First binomial token, lowercased — or None if abbreviated (single letter) / missing."""
    if not latin:
        return None
    tok = re.sub(r"[^a-z ]", " ", latin.lower()).split()
    if not tok or len(tok[0]) <= 1:
        return None
    return tok[0]


async def main():
    async with async_session() as db:
        plants = {str(p.id): p for p in (await db.execute(select(Plant))).scalars().all()}
        findings = (await db.execute(text(
            "SELECT id::text, entity_id::text, evidence FROM data_quality_findings "
            "WHERE check_id='alias.collision' AND status='open'"))).all()

        # owner_id -> list of (alias, C_plant) to DROP; plus review/ambiguous tallies
        drops: dict[str, list] = {}
        n_same_genus = n_ambig = n_triples = 0
        for fid, owner_id, ev in findings:
            if isinstance(ev, str):
                ev = json.loads(ev)
            O = plants.get(owner_id)
            if not O:
                continue
            og = genus(O.name_latin)
            for col in (ev or {}).get("collisions", []):
                alias = col.get("alias")
                for cw in col.get("collides_with", []):
                    n_triples += 1
                    C = plants.get(str(cw.get("id")))
                    if not C:
                        n_ambig += 1
                        continue
                    cg = genus(C.name_latin)
                    if og and cg and og != cg:
                        drops.setdefault(owner_id, []).append((alias, C))
                    elif og and cg and og == cg:
                        n_same_genus += 1
                    else:
                        n_ambig += 1

        n_drop = sum(len(v) for v in drops.values())
        print(f"findings {len(findings)} | triples {n_triples} | "
              f"DROP(diff-genus) {n_drop} on {len(drops)} owners | "
              f"same-genus(review) {n_same_genus} | ambiguous {n_ambig}")

        # estimate recipe re-routes
        sample = []
        reroute_total = 0
        # EXACT normalized match — move only ingredients named exactly the alias («укроп»→Укроп),
        # never substrings («укроп волосский» = a different plant, must stay).
        _SQLN = "btrim(regexp_replace(lower(name), '[^а-яёa-z0-9 ]+', '', 'g'))"
        async def count_reroute(oid, alias, cid):
            return (await db.execute(text(
                "SELECT count(*) FROM recipe_ingredients WHERE plant_id=:o AND " + _SQLN + " = :a"),
                {"o": oid, "a": norm(alias)})).scalar()

        for oid, lst in list(drops.items()):
            for alias, C in lst:
                rr = await count_reroute(oid, alias, str(C.id))
                reroute_total += rr
                if rr and len(sample) < 12:
                    sample.append((plants[oid].name, alias, C.name, rr))
        print(f"recipe links to re-route: {reroute_total}")
        for oname, alias, cname, rr in sample:
            print(f"   «{oname[:20]:20}» drop «{alias[:16]:16}» → re-route {rr:3} recipes → «{cname[:20]}»")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS alias_drop_audit (owner_id uuid, owner_name text, "
            "alias text, target_id uuid, target_name text, rerouted int, at timestamptz DEFAULT now())"))
        dropped = rerouted = 0
        for oid, lst in drops.items():
            O = plants[oid]
            hist = list(O.names_historical or [])
            for alias, C in lst:
                # re-route the recipes O captured via this alias onto C (exact normalized name)
                res = await db.execute(text(
                    "UPDATE recipe_ingredients SET plant_id=:c WHERE plant_id=:o AND " + _SQLN + " = :a"),
                    {"c": str(C.id), "o": oid, "a": norm(alias)})
                rr = res.rowcount or 0
                rerouted += rr
                hist = [h for h in hist if norm(h) != norm(alias)]
                await db.execute(text(
                    "INSERT INTO alias_drop_audit (owner_id,owner_name,alias,target_id,target_name,rerouted) "
                    "VALUES (:o,:on,:a,:c,:cn,:rr)"),
                    {"o": oid, "on": O.name, "a": alias, "c": str(C.id), "cn": C.name, "rr": rr})
                dropped += 1
            O.names_historical = hist or None
        # mark a finding resolved when its owner had only diff-genus drops handled
        await db.commit()
        print(f"\ndropped {dropped} bad aliases | re-routed {rerouted} recipe links. audit: alias_drop_audit.")


if __name__ == "__main__":
    asyncio.run(main())
