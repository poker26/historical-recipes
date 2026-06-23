# -*- coding: utf-8 -*-
"""Card-identity cleanup — merge latin/garbage-named cards into their Russian GENUS HUB.

~966 cards carry a latin-only / OCR-garbage primary `name` (Ferula, Chamomilla, Artemisia,
«Втазяса Г.»). iNat latin→Russian resolves only ~10% (genus-only latins / obscure fungi), so
external authority is insufficient. But most are DUPLICATE SHADOWS of a proper Russian card we
already have: the grounded fix is to merge them into the corpus's own genus hub.

SAFE RULE (genus-hub only): merge source S into target T iff T.rank='genus', T's latin GENUS ==
S's latin genus, and T.name is clean Russian (no latin letters). This is taxonomically safe
(same genus, into the hub) and AVOIDS the dangerous species-sibling matches the validation
exposed (Boletus luteus→badius = different species; Nardus→«Перелеска» = wrong-latin target).
No iNat/LLM — purely corpus-grounded. Richest card survives (the hub); S's children repoint, its
name/aliases fold into the hub, S is deleted. Audited in `card_merge_audit` (reversible-ish).

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/merge_genus_hub_cards.py
    ACTIVE only:    … -e SCOPE=active …      (cards with recipe links — the user-visible slice)
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import os

from sqlalchemy import select, update, text

from app.database import async_session
from app.models.plant import Plant, PlantCompatibility
from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient
from app.services.plant_matching import _PLANT_CHILD_MODELS

APPLY = bool(os.environ.get("APPLY"))
ACTIVE_ONLY = os.environ.get("SCOPE") == "active"


async def main():
    async with async_session() as db:
        active_clause = (" AND (SELECT count(*) FROM recipe_ingredients ri WHERE ri.plant_id=p.id) > 0"
                         if ACTIVE_ONLY else "")
        sources = (await db.execute(text(
            "SELECT p.id::text, p.name, p.name_latin, "
            "  (SELECT count(*) FROM recipe_ingredients ri WHERE ri.plant_id=p.id) nri "
            "FROM plants p WHERE p.name ~ '[A-Za-z]' AND p.name_latin IS NOT NULL" + active_clause))).all()

        pairs = []
        for sid, sname, slat, nri in sources:
            genus = (slat or "").strip().split()[0].lower()
            if len(genus) < 3:
                continue
            t = (await db.execute(text(
                "SELECT p.id::text, p.name, "
                "  (SELECT count(*) FROM recipe_ingredients ri WHERE ri.plant_id=p.id) n "
                "FROM plants p WHERE p.rank='genus' AND p.name !~ '[A-Za-z]' "
                "  AND lower(split_part(p.name_latin,' ',1))=:g AND p.id::text<>:sid "
                "ORDER BY n DESC LIMIT 1"), {"g": genus, "sid": sid})).first()
            if t:
                pairs.append((sid, sname, slat, nri, t[0], t[1]))

        print(f"sources: {len(sources)} | genus-hub merges: {len(pairs)} "
              f"({'ACTIVE only' if ACTIVE_ONLY else 'ALL'})")
        for sid, sname, slat, nri, tid, tname in pairs[:40]:
            print(f"  [{nri:4}] {sname[:24]:24} ({(slat or '')[:20]:20}) -> {tname[:26]}")
        if len(pairs) > 40:
            print(f"  … +{len(pairs)-40} more")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1 to merge.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS card_merge_audit (source_id uuid, source_name text, "
            "source_latin text, target_id uuid, target_name text, at timestamptz DEFAULT now())"))
        deleted_qdrant = []
        for sid, sname, slat, nri, tid, tname in pairs:
            src = (await db.execute(select(Plant).where(Plant.id == sid))).scalar_one()
            tgt = (await db.execute(select(Plant).where(Plant.id == tid))).scalar_one()
            await db.execute(text(
                "INSERT INTO card_merge_audit (source_id,source_name,source_latin,target_id,target_name) "
                "VALUES (:s,:sn,:sl,:t,:tn)"),
                {"s": sid, "sn": sname, "sl": slat, "t": tid, "tn": tname})
            # repoint every plant-scoped child + cross-domain link onto the hub
            for model in _PLANT_CHILD_MODELS:
                await db.execute(update(model).where(model.plant_id == src.id).values(plant_id=tgt.id))
            await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_a_id == src.id).values(plant_a_id=tgt.id))
            await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_b_id == src.id).values(plant_b_id=tgt.id))
            await db.execute(update(RecipeIngredient).where(RecipeIngredient.plant_id == src.id).values(plant_id=tgt.id))
            await db.execute(update(Ingredient).where(Ingredient.plant_id == src.id).values(plant_id=tgt.id))
            # keep the source's distinct name as a historical alias on the hub
            hist = list(tgt.names_historical or [])
            for h in (src.names_historical or []):
                if h and h not in hist:
                    hist.append(h)
            if src.name and src.name not in hist:
                hist.append(src.name)
            tgt.names_historical = hist or None
            if src.qdrant_point_id or src.qdrant_collection:
                deleted_qdrant.append((src.qdrant_collection or "plants_v2", src.qdrant_point_id))
            await db.delete(src)
        await db.commit()

    # purge the merged cards' now-orphaned qdrant points
    from app.services import qdrant
    by_coll: dict = {}
    for coll, pid in deleted_qdrant:
        if pid:
            by_coll.setdefault(coll, []).append(pid)
    purged = 0
    for coll, pids in by_coll.items():
        try:
            await qdrant.delete_points(coll, pids)
            purged += len(pids)
        except Exception as e:  # noqa: BLE001
            print(f"  qdrant purge failed ({coll}): {e}")
    print(f"\nmerged {len(pairs)} cards into genus hubs. audit: card_merge_audit. "
          f"qdrant points purged: {purged}")


if __name__ == "__main__":
    asyncio.run(main())
