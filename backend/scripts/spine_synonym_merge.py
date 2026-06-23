# -*- coding: utf-8 -*-
"""Use the Cherepanov spine to MERGE synonymous cards — the taxonomy-backbone payoff.

The spine (`taxon_backbone` accepted + `taxon_synonym` synonym→accepted, OCR'd from Cherepanov
1995) is the EXTERNAL truth for synonymy. It lets us merge cards that are the SAME species under
DIFFERENT latin names — which neither genus-hub merge nor latin-key dedup can do: e.g. «Адонис
весенний» (Adonis vernalis) + «горицвет весенний» (Adonanthe vernalis, a synonym) + «Желтоцвет
весенний» all resolve to one accepted_key.

resolve(latin) → accepted_key: a backbone hit returns its own key; a synonym returns its
accepted's key; else None. Corpus cards are grouped by accepted_key; any key with >1 card is a
synonym-duplicate group → merge into the best survivor (clean Russian name preferred, then richest
by facts). Reuses the proven child-repoint; audit `card_merge_audit`; qdrant purged.

    DRY (default):  docker compose exec -T -e PYTHONPATH=/app backend python scripts/spine_synonym_merge.py
    APPLY:          … -e APPLY=1 …
"""
import asyncio
import os
import re

from sqlalchemy import select, update, text

from app.database import async_session
from app.models.plant import Plant, PlantCompatibility
from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient
from app.services.plant_matching import _PLANT_CHILD_MODELS, _latin_key

APPLY = bool(os.environ.get("APPLY"))


async def main():
    async with async_session() as db:
        acc = {r[0] for r in (await db.execute(text(
            "SELECT DISTINCT accepted_key FROM taxon_backbone WHERE accepted_key IS NOT NULL"))).all()}
        syn = {r[0]: r[1] for r in (await db.execute(text(
            "SELECT syn_key, accepted_key FROM taxon_synonym "
            "WHERE syn_key IS NOT NULL AND accepted_key IS NOT NULL"))).all()}

        def resolve(latin):
            k = _latin_key(latin)
            if not k:
                return None
            if k in acc:
                return k
            a = syn.get(k)
            return a if a in acc else None

        plants = (await db.execute(select(Plant))).scalars().all()
        groups: dict[str, list] = {}
        for p in plants:
            ak = resolve(p.name_latin)
            if ak:
                groups.setdefault(ak, []).append(p)
        merge_groups = {k: ps for k, ps in groups.items() if len(ps) > 1}

        # fact counts for survivor ranking
        ids = [p.id for ps in merge_groups.values() for p in ps]
        cnt = {}
        if ids:
            for r in (await db.execute(text(
                "SELECT plant_id::text, count(*) FROM recipe_ingredients WHERE plant_id = ANY(:ids) "
                "GROUP BY 1"), {"ids": [str(i) for i in ids]})).all():
                cnt[r[0]] = r[1]

        def clean_ru(p):
            return bool(p.name) and not re.search(r"[A-Za-z]", p.name)

        def rank(p):
            return (clean_ru(p), cnt.get(str(p.id), 0), len(p.name or ""))

        # SAFE rule: merge only LATIN-named shadow losers (no Russian identity to lose; spine
        # confirms the species). Skip Russian-named losers — a wrong corpus latin would mis-merge
        # two real cards (Picea pungens card mislabeled «Picea abies», Aegopodium→Petasites). Those
        # Russian-vs-Russian synonym merges need review.
        plan = []
        skipped_ru = 0
        for ak, ps in merge_groups.items():
            survivor = max(ps, key=rank)
            losers, ru_losers = [], 0
            for p in ps:
                if p.id == survivor.id:
                    continue
                if re.search(r"[A-Za-z]", p.name or ""):
                    losers.append(p)
                else:
                    ru_losers += 1
            skipped_ru += ru_losers
            if losers:
                plan.append((ak, survivor, losers))

        n_loss = sum(len(l) for _, _, l in plan)
        print(f"backbone keys {len(acc)} | synonyms {len(syn)} | corpus cards resolved {sum(len(v) for v in groups.values())}")
        print(f"SAFE latin-shadow merges: {len(plan)} groups / {n_loss} cards | "
              f"Russian-vs-Russian losers skipped (review): {skipped_ru}\n")
        for ak, surv, losers in sorted(plan, key=lambda x: -len(x[2]))[:18]:
            ln = ", ".join((l.name or "?")[:18] for l in losers[:3])
            print(f"  [{ak[:22]:22}] keep «{(surv.name or '?')[:24]:24}» ⨉ merge: {ln}{' …' if len(losers)>3 else ''}")

        if not APPLY:
            print("\nDRY — nothing changed. Set APPLY=1.")
            return

        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS card_merge_audit (source_id uuid, source_name text, "
            "source_latin text, target_id uuid, target_name text, at timestamptz DEFAULT now())"))
        deleted_qdrant = []
        merged = 0
        for ak, survivor, losers in plan:
            hist = list(survivor.names_historical or [])
            for src in losers:
                await db.execute(text(
                    "INSERT INTO card_merge_audit (source_id,source_name,source_latin,target_id,target_name) "
                    "VALUES (:s,:sn,:sl,:t,:tn)"),
                    {"s": str(src.id), "sn": src.name, "sl": src.name_latin,
                     "t": str(survivor.id), "tn": survivor.name})
                for model in _PLANT_CHILD_MODELS:
                    await db.execute(update(model).where(model.plant_id == src.id).values(plant_id=survivor.id))
                await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_a_id == src.id).values(plant_a_id=survivor.id))
                await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_b_id == src.id).values(plant_b_id=survivor.id))
                await db.execute(update(RecipeIngredient).where(RecipeIngredient.plant_id == src.id).values(plant_id=survivor.id))
                await db.execute(update(Ingredient).where(Ingredient.plant_id == src.id).values(plant_id=survivor.id))
                for h in (src.names_historical or []):
                    if h and h not in hist:
                        hist.append(h)
                if src.name and src.name not in hist:
                    hist.append(src.name)
                if src.qdrant_point_id or src.qdrant_collection:
                    deleted_qdrant.append((src.qdrant_collection or "plants_v2", src.qdrant_point_id))
                await db.delete(src)
                merged += 1
            survivor.names_historical = hist or None
        await db.commit()

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
    print(f"\nmerged {merged} synonym-duplicate cards. audit: card_merge_audit. qdrant purged: {purged}")


if __name__ == "__main__":
    asyncio.run(main())
