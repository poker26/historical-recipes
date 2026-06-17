"""identity.conflict check: name↔latin corruption. A card whose Russian name is a
clean genus (Водосбор = Aquilegia) but whose stored latin is a DIFFERENT genus
(«Aconitum vulgare») — the latin was mis-glued. Resolve the Russian name via iNat
(the grounded source of truth); if iNat strongly matches the name but returns a
DIFFERENT genus than the stored latin, flag a conflict and propose the iNat latin
(GBIF-verified, kingdom-gated against homonyms).

Usage:  python identity_conflict.py pilot [N]   — sample, print conflicts, NO writes
"""
import asyncio
import json
import re
import sys

import httpx
from sqlalchemy import text

from app.database import async_session
from app.temporal.cleanup_activities import _inat_by_ru, _strong, _king_ok
from app.services.data_quality.taxonomy import GBIF_MATCH_URL

_LAT = re.compile(r"[A-Za-z]+")


def genus_of(latin):
    t = _LAT.findall(latin or "")
    return t[0].lower() if t else None


async def _gbif_full(client, sci, cache):
    """GBIF match → {speciesKey, status, canonical, kingdom}. speciesKey is the
    ACCEPTED species — two synonyms (Festuca pratensis / Lolium pratense) share it,
    so a genus disagreement with the SAME speciesKey is reclassification, not
    corruption."""
    if not sci:
        return None
    if sci in cache:
        return cache[sci]
    out = None
    try:
        r = await client.get(GBIF_MATCH_URL, params={"name": sci})
        d = r.json()
        if d.get("matchType") and d.get("matchType") != "NONE":
            out = {"speciesKey": d.get("speciesKey"), "status": d.get("status"),
                   "canonical": d.get("canonicalName") or d.get("scientificName"),
                   "kingdom": d.get("kingdom"), "match": d.get("matchType")}
    except Exception:
        out = None
    cache[sci] = out
    return out


CHECK = "identity.conflict"


async def _record(pid, status, ev):
    async with async_session() as s:
        await s.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (gen_random_uuid(), :c, 'P0', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:ev AS jsonb), false, :st, now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              evidence=EXCLUDED.evidence, status=EXCLUDED.status, last_seen=now()
        """), {"c": CHECK, "eid": pid, "title": (ev.get("name") or "")[:120],
               "ev": json.dumps(ev, ensure_ascii=False), "st": status})
        await s.commit()


async def _fix(pid, latin):
    async with async_session() as s:
        await s.execute(text(
            "UPDATE plants SET name_latin=:l, inat_synced_at=NULL WHERE id=CAST(:i AS uuid)"),
            {"l": latin, "i": pid})
        await s.commit()


async def main():
    pilot = len(sys.argv) > 1 and sys.argv[1] == "pilot"
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 150)
    async with async_session() as s:
        if pilot:
            rows = (await s.execute(text("""
                SELECT id, name, name_latin, kingdom FROM plants
                WHERE name ~ '[А-Яа-яЁё]' AND name_latin ~ '^[A-Za-z]+ [a-z]+'
                ORDER BY random() LIMIT :n"""), {"n": n})).all()
        else:
            rows = (await s.execute(text("""
                SELECT id, name, name_latin, kingdom FROM plants p
                WHERE name ~ '[А-Яа-яЁё]' AND name_latin ~ '^[A-Za-z]+ [a-z]+'
                  AND p.id::text NOT IN (SELECT entity_id FROM data_quality_findings
                                         WHERE check_id=:c AND status='resolved')"""), {"c": CHECK})).all()

    conflicts = fixed = staged = agree = nomatch = synonym = 0
    gcache, icache = {}, {}
    async with httpx.AsyncClient(timeout=30) as client:
        for idx, (pid, name, latin, kingdom) in enumerate(rows):
            inat = await _inat_by_ru(client, name, icache)
            await asyncio.sleep(0.7)
            sci = (inat or {}).get("sci")
            if not sci or not _strong(name, (inat or {}).get("ru")):
                nomatch += 1
                continue
            ig, sg = genus_of(sci), genus_of(latin)
            if not ig or ig == sg:
                agree += 1
                continue
            ginat = await _gbif_full(client, sci, gcache)
            gstore = await _gbif_full(client, latin, gcache)
            ik, sk = (ginat or {}).get("speciesKey"), (gstore or {}).get("speciesKey")
            if ik and sk and ik == sk:
                synonym += 1
                continue
            ok = bool(ginat and (ginat.get("match") or "").upper() in ("EXACT", "FUZZY")
                      and _king_ok(kingdom, ginat.get("kingdom")))
            proposed = (ginat or {}).get("canonical") or sci
            conflicts += 1
            ev = {"name": name, "stored": latin, "proposed": proposed,
                  "inat_ru": inat.get("ru"), "gbif_ok": ok}
            if pilot:
                print(f"  [{'✓gbif' if ok else '?gbif'}] {name!r}: {latin!r} → {proposed!r}")
            elif ok:
                await _fix(str(pid), proposed)
                await _record(str(pid), "resolved", ev)
                fixed += 1
            else:
                await _record(str(pid), "open", ev)
                staged += 1
            if not pilot and conflicts % 20 == 0:
                print(f"  ..{idx+1}/{len(rows)} scanned: fixed={fixed} staged={staged}", flush=True)

    print(f"DONE scanned={len(rows)} conflicts={conflicts} (fixed={fixed} staged={staged}) "
          f"synonyms={synonym} agree={agree} no-match={nomatch}", flush=True)


asyncio.run(main())
