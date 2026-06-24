# -*- coding: utf-8 -*-
"""Name↔latin cross-check — gate the latin-key consolidation by verifying each card's RUSSIAN
name agrees with its latin (the wall: «Просвирник»=Malva mislabeled `abelmoschus` defeats every
latin-only authority).

Reuses the VALIDATED latin-repair pipeline (fix_latin_run): iNat-by-Russian-name = truth, GBIF
normalize + kingdom gate. For each card in a latin-key dup group, resolve its Russian name → the
expected genus; compare to the card's latin genus:
  * CONSISTENT — name's genus == latin's genus → the card's identity is trustworthy.
  * MISMATCH   — different genus → the latin is wrong vs the name → FLAG, never merge.
  * SHADOW     — latin-named card (no Russian identity to mismatch) → safe to merge in.
  * UNKNOWN    — iNat gave nothing → can't verify → conservatively blocks its group.

A group is SAFE iff every Russian-named card in it is CONSISTENT (shadows allowed, no MISMATCH,
no UNKNOWN). Safe groups are merged (clean-Russian, richest survivor) reusing the proven
child-repoint; MISMATCH cards are staged as `identity.name_latin_mismatch` findings.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/name_latin_crosscheck.py
    APPLY:          … -e APPLY=1 …      (DQ_LIMIT=N caps groups for a trial)
"""
import asyncio
import os
import re
import uuid

import httpx
from sqlalchemy import select, update, text

from app.database import async_session
from app.models.plant import Plant, PlantCompatibility
from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient
from app.services.plant_matching import _PLANT_CHILD_MODELS, _latin_key
from app.services.data_quality.taxonomy import _resolve_one
from app.services.inaturalist import INAT_BASE, _HEADERS

APPLY = bool(os.environ.get("APPLY"))
LIMIT = int(os.getenv("DQ_LIMIT", "0"))
CONCURRENCY = 3
_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")
_gbif_cache: dict = {}


def ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def lat_genus(s):
    t = _LAT.findall(s or "")
    return t[0].lower() if t else ""


def king_ok(card_kingdom, gbif_kingdom):
    if not gbif_kingdom:
        return False
    if (card_kingdom or "").startswith("гриб"):
        return gbif_kingdom == "Fungi"
    return gbif_kingdom in ("Plantae", "Chromista")


async def gbif_acc(client, latin):
    """GBIF accepted usage-key + kingdom for a latin (acceptedUsageKey for synonyms, usageKey
    for accepted) — so synonym genera (Argentina/Potentilla, Laricifomes/Fomitopsis) collapse to
    ONE key. None if unresolvable / low-confidence."""
    key = " ".join(_LAT.findall(latin or "")[:2]).lower()
    if not key:
        return None
    if key in _gbif_cache:
        return _gbif_cache[key]
    try:
        d = (await client.get("https://api.gbif.org/v1/species/match",
                              params={"name": latin}, headers=_HEADERS)).json()
    except Exception:
        return None  # transient — don't cache
    res = None
    if (d.get("matchType") or "").upper() in ("EXACT", "FUZZY") and (d.get("confidence") or 0) >= 85:
        canon = d.get("canonicalName")
        if (d.get("status") or "").upper().endswith("SYNONYM") and d.get("acceptedUsageKey"):
            try:
                sp = (await client.get(f"https://api.gbif.org/v1/species/{d['acceptedUsageKey']}",
                                       headers=_HEADERS)).json()
                canon = sp.get("canonicalName") or canon
            except Exception:
                pass
        # compare on the ACCEPTED canonical NAME (latin_key) — unifies GBIF backbone duplicates
        # (Nepeta nuda 7309059/7309060) AND synonym genera (Argentina/Potentilla anserina).
        if canon:
            res = {"acc": _latin_key(canon), "kingdom": d.get("kingdom")}
    _gbif_cache[key] = res
    return res


async def inat_by_ru(client, name):
    qs = [name]
    w = ruwords(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    if w:
        qs.append(w[0])
    for q in qs:
        try:
            resp = await client.get(f"{INAT_BASE}/taxa",
                                    params={"q": q, "locale": "ru", "per_page": 5, "is_active": "true"},
                                    headers=_HEADERS)
        except Exception:
            return None
        if resp.status_code == 429:
            await asyncio.sleep(5)
            continue
        if resp.status_code != 200:
            break
        sp = [r for r in resp.json().get("results", []) if r.get("rank") == "species"]
        if sp:
            return {"sci": sp[0].get("name")}
        break
    return None


async def classify(client, sem, p):
    """→ (status, inat_sci). status ∈ CONSISTENT|MISMATCH|SHADOW|UNKNOWN. CONSISTENT iff the
    card's latin and its Russian-name's iNat latin resolve to the SAME GBIF accepted taxon."""
    if re.search(r"[A-Za-z]", p.name or ""):
        return "SHADOW", None
    if len(ruwords(p.name)) < 2:            # single-word = genus-level name; iNat picks a random
        return "UNKNOWN", None              # species → unverifiable against a species latin
    async with sem:
        card_g = await gbif_acc(client, p.name_latin)
        inat = await inat_by_ru(client, p.name)
        inat_sci = (inat or {}).get("sci")
        inat_g = await gbif_acc(client, inat_sci) if inat_sci else None
    if not inat_g or not king_ok(p.kingdom, inat_g.get("kingdom")):
        return "UNKNOWN", inat_sci          # name not verifiable / cross-kingdom
    if not card_g:
        return "UNKNOWN", inat_sci          # card latin unresolvable
    return ("CONSISTENT" if card_g["acc"] == inat_g["acc"] else "MISMATCH"), inat_sci


async def stage_mismatch(pid, name, latin, expected):
    async with async_session() as s:
        await s.execute(text(
            "INSERT INTO data_quality_findings (id,check_id,severity,entity_type,entity_id,title,"
            "evidence,auto_fixable,status,first_seen,last_seen) VALUES "
            "(gen_random_uuid(),'identity.name_latin_mismatch','P1','plant',CAST(:e AS uuid),:t,"
            "CAST(:ev AS jsonb),false,'open',now(),now()) "
            "ON CONFLICT (check_id, entity_id) DO UPDATE SET evidence=EXCLUDED.evidence, last_seen=now()"),
            {"e": str(pid), "t": f"«{name}»: имя↔латынь расходятся (имя→{expected}, латынь {latin})",
             "ev": f'{{"name_genus":"{expected}","latin":"{latin}"}}'})
        await s.commit()


async def main():
    async with async_session() as db:
        plants = (await db.execute(select(Plant))).scalars().all()
    groups: dict[str, list] = {}
    for p in plants:
        k = _latin_key(p.name_latin)
        if k:
            groups.setdefault(k, []).append(p)
    dup = [(k, ps) for k, ps in groups.items() if len(ps) > 1]
    if LIMIT:
        dup = dup[:LIMIT]
    cards = [p for _, ps in dup for p in ps]
    print(f"latin-key dup groups: {len(dup)} | cards to cross-check: {len(cards)}", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    status: dict = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(cards), 60):
            batch = cards[i:i + 60]
            res = await asyncio.gather(*[classify(client, sem, p) for p in batch])
            for p, (st, exp) in zip(batch, res):
                status[p.id] = (st, exp)
            print(f"  ...{min(i+60,len(cards))}/{len(cards)} cross-checked", flush=True)

    safe, blocked, mism = [], [], []
    for k, ps in dup:
        sts = [status[p.id][0] for p in ps]
        if "MISMATCH" in sts or "UNKNOWN" in sts:
            blocked.append((k, ps))
            for p in ps:
                if status[p.id][0] == "MISMATCH":
                    mism.append((p, status[p.id][1]))
        elif any(s == "CONSISTENT" for s in sts):
            safe.append((k, ps))
    print(f"\nSAFE groups (all Russian cards consistent): {len(safe)} | "
          f"blocked (mismatch/unknown): {len(blocked)} | mismatches to flag: {len(mism)}", flush=True)
    for p, exp in mism[:12]:
        print(f"   MISMATCH «{(p.name or '?')[:24]:24}» latin {(p.name_latin or '')[:20]:20} (name→{exp})")
    for k, ps in safe[:10]:
        cons = [p for p in ps if status[p.id][0] == "CONSISTENT"]
        surv = max(cons, key=lambda p: (not re.search(r"[A-Za-z]", p.name or ""), len(p.name or "")))
        print(f"   SAFE [{k[:18]:18}] keep «{(surv.name or '?')[:22]:22}» ⨉{len(ps)-1}")

    if not APPLY:
        print("\nDRY — nothing changed. Set APPLY=1.", flush=True)
        return

    for p, exp in mism:
        await stage_mismatch(p.id, p.name, p.name_latin, exp)

    deleted_qdrant = []
    merged = 0
    async with async_session() as db:
        await db.execute(text("CREATE TABLE IF NOT EXISTS card_merge_audit (source_id uuid, "
                              "source_name text, source_latin text, target_id uuid, target_name text, "
                              "at timestamptz DEFAULT now())"))
        for k, ps in safe:
            keep = [p for p in ps if status[p.id][0] in ("CONSISTENT", "SHADOW")]
            survivor = max(keep, key=lambda p: (not re.search(r"[A-Za-z]", p.name or ""),
                                                status[p.id][0] == "CONSISTENT", len(p.name or "")))
            losers = [p for p in keep if p.id != survivor.id]
            if not losers:
                continue
            surv = await db.get(Plant, survivor.id)
            hist = list(surv.names_historical or [])
            for src in losers:
                s = await db.get(Plant, src.id)
                if not s:
                    continue
                await db.execute(text(
                    "INSERT INTO card_merge_audit (source_id,source_name,source_latin,target_id,target_name) "
                    "VALUES (:s,:sn,:sl,:t,:tn)"),
                    {"s": str(s.id), "sn": s.name, "sl": s.name_latin, "t": str(surv.id), "tn": surv.name})
                for model in _PLANT_CHILD_MODELS:
                    await db.execute(update(model).where(model.plant_id == s.id).values(plant_id=surv.id))
                await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_a_id == s.id).values(plant_a_id=surv.id))
                await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_b_id == s.id).values(plant_b_id=surv.id))
                await db.execute(update(RecipeIngredient).where(RecipeIngredient.plant_id == s.id).values(plant_id=surv.id))
                await db.execute(update(Ingredient).where(Ingredient.plant_id == s.id).values(plant_id=surv.id))
                for h in (s.names_historical or []):
                    if h and h not in hist:
                        hist.append(h)
                if s.name and s.name not in hist:
                    hist.append(s.name)
                if s.qdrant_point_id or s.qdrant_collection:
                    deleted_qdrant.append((s.qdrant_collection or "plants_v2", s.qdrant_point_id))
                await db.delete(s)
                merged += 1
            surv.names_historical = hist or None
        await db.commit()

    from app.services import qdrant
    by_coll: dict = {}
    for coll, pid in deleted_qdrant:
        if pid:
            by_coll.setdefault(coll, []).append(pid)
    for coll, pids in by_coll.items():
        try:
            await qdrant.delete_points(coll, pids)
        except Exception as e:  # noqa: BLE001
            print(f"  qdrant purge failed ({coll}): {e}")
    print(f"\nmerged {merged} cross-check-verified cards | flagged {len(mism)} name↔latin mismatches.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
