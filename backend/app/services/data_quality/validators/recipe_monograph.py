"""domain.recipe_is_monograph (P1, auto-purgeable) — fake recipe = a plant monograph.

In plant-atlas / monograph herbalism books the recipe extractor over-triggers on
monograph sections and emits one fake `category='сбор'` recipe per plant, named
after the bare species (e.g. «Бузина черная», «Береза повислая»). The monograph
itself is captured correctly via extract_plant_entries, so the recipe is additive
dup-noise: cheap to purge, no data loss.

Signature (v1, conservative): `category='сбор'` AND the recipe name, normalized,
equals a known plant primary name. (The stronger "all ingredients are phytochemical
compounds" leg is left for v2 — name=species already isolates the class cleanly.)
Fix = delete recipe row + its recipes_v2 qdrant points.
"""
from sqlalchemy import select

from app.models.plant import Plant
from app.models.recipe import Recipe
from app.services.data_quality.framework import Finding, norm, validator

_MONOGRAPH_CATEGORIES = ("сбор",)


@validator("domain.recipe_is_monograph", severity="P1", auto_fixable=True,
           description="category=сбор recipe named exactly after a plant species")
async def check_recipe_is_monograph(db) -> list[Finding]:
    plant_names = {norm(n) for (n,) in (await db.execute(select(Plant.name))).all()}

    rows = (await db.execute(
        select(Recipe.id, Recipe.name, Recipe.category, Recipe.book_id)
        .where(Recipe.category.in_(_MONOGRAPH_CATEGORIES))
    )).all()

    findings: list[Finding] = []
    for rid, name, category, book_id in rows:
        if norm(name) not in plant_names:
            continue
        findings.append(Finding(
            check_id="domain.recipe_is_monograph", severity="P1",
            entity_type="recipe", entity_id=str(rid), auto_fixable=True,
            title=f"Рецепт-монография: «{name}» (category={category}) = имя растения",
            evidence={"recipe": name, "category": category,
                      "book_id": str(book_id) if book_id else None},
            suggested_fix={"action": "delete_recipe_and_qdrant", "recipe_id": str(rid)},
        ))
    return findings
