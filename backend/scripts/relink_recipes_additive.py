"""Additive recipe→plant relink — fill ONLY the NULL links, never clear existing.

The global `relink_recipe_ingredients` is destructive (clears every link, re-captures) and is
gated by the 378 open `alias.collision` findings (a bad folk alias like Ежевика→«Дереза»=Lycium
would steal the real plant's recipes on a full re-capture). This pass instead:

  * matches ONLY ingredients / recipe-ingredients whose plant_id IS NULL (existing correct links
    are never touched → no blast radius on the 44k good links);
  * SUPPRESSES the 378 colliding aliases — strips each colliding alias from its OWNER plant's
    names_historical in MATCHER-ONLY shadow objects (no data mutation), so the plant that holds
    the string as its PRIMARY name wins and the bad alias can't mis-route.

Net: most of the 8889 currently-unlinked recipes gain a plant link (big «что приготовить»
coverage win) at low risk, without re-entering the identity domain. The full collision cleanup
+ global relink stays as later hygiene.

    DRY_RUN=1 docker compose exec -T -e PYTHONPATH=/app backend python scripts/relink_recipes_additive.py
            docker compose exec -T -e PYTHONPATH=/app backend python scripts/relink_recipes_additive.py
"""
import asyncio
import os
import re
from types import SimpleNamespace

from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant
from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.services.plant_matching import PlantMatcher, normalize

DRY = bool(os.environ.get("DRY_RUN"))

# Prep-form «plant» cards — «сироп крушины», «Масло сливочное», «сбор успокоительный» — are
# preparations, NOT canonical plants; their prep word seeds a polluting noun-token magnet
# («сироп» catches every sugar syrup). Omit them as match targets: the real plant stays
# reachable via its own token («масло герани» → «геран» → Герань). Word-boundary space-suffix
# so single-word real plants are spared (Бальзамин ≠ «бальзам », Чайот, басбас=Myristica).
_PREP_FORM = re.compile(
    r"^(сироп|масло|настойк\w*|насто[йя]\w*|отвар|сбор|экстракт|вытяжк\w*|тинктур\w*|"
    r"эликсир|порошок|капли|эссенц\w*|мазь|припарк\w*|примочк\w*|компресс|бальзам)\s+\S", re.I)


async def main():
    async with async_session() as db:
        plants = (await db.execute(select(Plant))).scalars().all()

        # owner plant id -> {normalized colliding alias strings to suppress}
        findings = (await db.execute(text(
            "SELECT entity_id, evidence FROM data_quality_findings "
            "WHERE check_id='alias.collision' AND status='open'"))).all()
        suppress: dict[str, set[str]] = {}
        for entity_id, ev in findings:
            if not entity_id or not isinstance(ev, dict):
                continue
            al = {normalize(c.get("alias")) for c in ev.get("collisions", []) if c.get("alias")}
            al.discard(None)
            if al:
                suppress.setdefault(str(entity_id), set()).update(al)
        n_supp = sum(len(v) for v in suppress.values())
        print(f"open alias.collision: {len(findings)} findings | suppressing {n_supp} alias(es) "
              f"on {len(suppress)} plants")

        # prep-form cards are suppressed BOTH as matcher targets AND as inheritance targets:
        # the canonical ingredient may already carry a stale wrong link to one (e.g. «сахарный
        # сироп» → «сироп крушины»), which a NULL recipe-ingredient would otherwise inherit.
        suppressed_pids = {str(p.id) for p in plants if _PREP_FORM.match((p.name or "").strip())}
        shadows = []
        for p in plants:
            if str(p.id) in suppressed_pids:
                continue
            kill = suppress.get(str(p.id))
            hist = list(p.names_historical or [])
            if kill:
                hist = [h for h in hist if normalize(h) not in kill]
            shadows.append(SimpleNamespace(
                id=p.id, name=p.name, name_latin=p.name_latin,
                names_historical=hist, rank=getattr(p, "rank", None)))
        print(f"suppressed {len(suppressed_pids)} prep-form magnet cards (matcher + inheritance)")
        matcher = PlantMatcher(shadows)

        syn_by_ing: dict = {}
        for s in (await db.execute(select(IngredientSynonym))).scalars().all():
            syn_by_ing.setdefault(s.ingredient_id, []).append(s.synonym)

        # 1) NULL canonical ingredients → plant
        ings = (await db.execute(select(Ingredient).where(Ingredient.plant_id.is_(None)))).scalars().all()
        ing_linked = 0
        samples = []
        for ing in ings:
            pid = matcher.match([ing.canonical_name, *syn_by_ing.get(ing.id, [])])
            if pid:
                if not DRY:
                    ing.plant_id = pid
                ing_linked += 1
        ing_now = {i.id: i.plant_id for i in ings}  # in-memory new links (for RI inherit)

        # 2) NULL recipe-ingredients → plant (inherit canonical link, else match names)
        ris = (await db.execute(select(RecipeIngredient).where(RecipeIngredient.plant_id.is_(None)))).scalars().all()
        ri_linked = 0
        by_target: dict = {}
        all_ings = {i.id: i for i in (await db.execute(select(Ingredient))).scalars().all()}
        for ri in ris:
            pid = None
            ing = all_ings.get(ri.ingredient_id) if ri.ingredient_id else None
            cur = ing_now.get(ing.id, ing.plant_id) if ing is not None else None
            if cur is not None and str(cur) in suppressed_pids:
                cur = None                       # don't inherit a stale link to a prep-form card
            if cur is not None:
                pid = cur
            else:
                names = [ri.name]
                if ing is not None:
                    names.append(ing.canonical_name)
                    names.extend(syn_by_ing.get(ing.id, []))
                pid = matcher.match(names)
            if pid:
                if not DRY:
                    ri.plant_id = pid
                ri_linked += 1
                if DRY:
                    by_target[str(pid)] = by_target.get(str(pid), 0) + 1

        if DRY:
            print(f"WOULD link: {ing_linked} ingredients, {ri_linked} recipe-ingredients")
            pname = {str(p.id): p.name for p in plants}
            top = sorted(by_target.items(), key=lambda kv: kv[1], reverse=True)[:18]
            print("top target plants (would-link RI count):")
            for pid, n in top:
                print(f"   {n:5}  {pname.get(pid,'?')[:40]:40}  {pid[:8]}")
            print("DRY_RUN — nothing changed.")
            return

        await db.commit()
        gained = (await db.execute(text(
            "SELECT count(*) FROM recipes r WHERE EXISTS(SELECT 1 FROM recipe_ingredients ri "
            "WHERE ri.recipe_id=r.id) AND NOT EXISTS(SELECT 1 FROM recipe_ingredients ri "
            "WHERE ri.recipe_id=r.id AND ri.plant_id IS NOT NULL)"))).scalar()
        print(f"linked {ing_linked} ingredients, {ri_linked} recipe-ingredients | "
              f"recipes still 0-plant: {gained}")


if __name__ == "__main__":
    asyncio.run(main())
