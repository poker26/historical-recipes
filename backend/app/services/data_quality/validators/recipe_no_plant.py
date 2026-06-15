"""domain.recipe_no_plant — recipe with no botanical component (or not a recipe).

A plant-recipe KB should not be serving recipes built entirely from minerals /
chemicals / animal matter — глина, керосин, дёготь, купорос, etc. Signal: the
recipe HAS ingredients but NONE of them resolved to a plant (`plant_id` null on
every ingredient).

Two confidence tiers via per-finding severity:
* **P1** — at least one ingredient hits the non-botanical lexicon (clay/kerosene/
  vitriol…): almost certainly a genuinely non-plant recipe.
* **P2** — no plant link AND no lexicon hit: could also be a matcher miss (a real
  plant the relinker failed to bind), so it's a review lead, not a verdict.

Fix is human (delete / reclassify / relink), never auto.
"""
from collections import defaultdict

from sqlalchemy import select

from app.models.recipe import Recipe, RecipeIngredient
from app.services.data_quality.framework import Finding, norm, validator

# Mineral / chemical / clearly non-botanical substances seen in old recipes.
# Matched as a substring of the normalized ingredient name (so «дёготь берёзовый»
# still hits «дёготь»). Kept high-precision — herbs are NOT here.
_NON_BOTANICAL = {
    "глина", "керосин", "нефть", "дёготь", "деготь", "купорос", "квасцы",
    "известь", "извёстка", "поташ", "скипидар", "нашатырь", "ртуть", "киноварь",
    "мышьяк", "сурьма", "свинец", "селитра", "гипс", "парафин", "вазелин",
    "нафталин", "карболк", "креозот", "гудрон", "охра", "мумиё", "мумие",
    "озокерит", "графит", "глауберова соль", "английская соль", "поваренная соль",
    "магнезия", "формалин", "сулема", "ляпис",
}


def _nonbotanical_hits(names: list[str]) -> list[str]:
    hits = []
    for n in names:
        nl = norm(n)
        if any(term in nl for term in _NON_BOTANICAL):
            hits.append(n)
    return hits


@validator("domain.recipe_no_plant", severity="P2", auto_fixable=False,
           description="recipe whose ingredients include no plant (P1 if a non-botanical substance is present)")
async def check_recipe_no_plant(db) -> list[Finding]:
    ings: dict = defaultdict(list)  # recipe_id -> [(plant_id, name)]
    for rid, pid, name in (await db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.plant_id, RecipeIngredient.name)
    )).all():
        ings[rid].append((pid, name))

    recipes = (await db.execute(
        select(Recipe.id, Recipe.name, Recipe.category, Recipe.book_id)
    )).all()

    findings: list[Finding] = []
    for rid, rname, category, book_id in recipes:
        items = ings.get(rid)
        if not items:
            continue  # zero-ingredient recipes are a separate (noisier) class — skip here
        if any(pid for pid, _ in items):
            continue  # has at least one plant-linked ingredient → ok
        names = [n for _, n in items]
        hits = _nonbotanical_hits(names)
        severity = "P1" if hits else "P2"
        title = f"Рецепт «{rname}» без растительных компонент"
        if hits:
            title += f" — небот. вещества: {', '.join(hits[:4])}"
        else:
            title += " (нет привязки к растениям — небот. рецепт ИЛИ промах матчера)"
        findings.append(Finding(
            check_id="domain.recipe_no_plant", severity=severity,
            entity_type="recipe", entity_id=str(rid),
            title=title,
            evidence={"recipe": rname, "category": category,
                      "book_id": str(book_id) if book_id else None,
                      "ingredients": names[:30], "non_botanical_hits": hits},
            suggested_fix={"action": "review_recipe", "recipe_id": str(rid),
                           "note": "нет растительных компонент: удалить / переклассифицировать / перепривязать"},
        ))
    return findings
