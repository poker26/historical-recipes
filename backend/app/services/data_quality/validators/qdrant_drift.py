"""index.qdrant_drift — Postgres ↔ Qdrant consistency. Purely STRUCTURAL (point id ==
entity UUID for plants/recipes), so no semantic ambiguity — the kind of signal pure-read
detection is actually good at.

* **P1 orphan points** — a Qdrant point whose entity no longer exists in Postgres
  (the card/recipe was deleted but its vector lingered). These are live search results
  that 404 on tap → a real defect. Bulk-deletable.
* **P2 lost recipe index** — a recipe we RECORDED as indexed (`qdrant_point_id` set) whose
  point is missing from Qdrant → it silently dropped out of search; needs re-index.

One SUMMARY finding per collection/direction (with a sample + capped id list), not one per
point — orphans are bulk-handled, and thousands of per-point rows would just be noise.
Plant «missing index» is intentionally NOT flagged (thin cards are unindexed by design —
that's the ambiguous direction; orphans are the clean one).
"""
from sqlalchemy import select

from app.config import settings
from app.models.plant import Plant
from app.models.recipe import Recipe
from app.services import qdrant
from app.services.data_quality.framework import Finding, validator


@validator("index.qdrant_drift", severity="P1", auto_fixable=False,
           description="Qdrant points orphaned from Postgres (deleted entity) or recipes lost from the index")
async def check_qdrant_drift(db) -> list[Finding]:
    findings: list[Finding] = []
    plant_ids = {str(r[0]) for r in (await db.execute(select(Plant.id))).all()}
    recipe_ids = {str(r[0]) for r in (await db.execute(select(Recipe.id))).all()}

    pairs = [(settings.qdrant_collection_herbalism, plant_ids, "растение"),
             (settings.qdrant_collection_recipes, recipe_ids, "рецепт")]
    for coll, pg_ids, label in pairs:
        try:
            q_ids = await qdrant.scroll_all_point_ids(coll)
        except Exception:
            continue   # qdrant unreachable → skip this collection silently
        orphans = sorted(q_ids - pg_ids)
        if orphans:
            findings.append(Finding(
                check_id="index.qdrant_drift", severity="P1",
                entity_type="qdrant", entity_id=f"{coll}:orphans",
                title=f"Qdrant «{coll}»: {len(orphans)} точек-сирот (сущность удалена из Postgres)",
                evidence={"collection": coll, "orphan_count": len(orphans),
                          "sample": orphans[:20], "ids": orphans[:2000]},
                suggested_fix={"action": "delete_orphan_points", "collection": coll,
                               "note": "bulk delete_points; non-destructive to Postgres"},
            ))

    # Lost recipe index — recorded indexed but the point is gone.
    try:
        indexed_q = await qdrant.scroll_all_point_ids(settings.qdrant_collection_recipes)
        recorded = {str(r[0]) for r in (await db.execute(
            select(Recipe.id).where(Recipe.qdrant_point_id.isnot(None)))).all()}
        lost = sorted(recorded - indexed_q)
        if lost:
            findings.append(Finding(
                check_id="index.qdrant_drift", severity="P2",
                entity_type="qdrant", entity_id=f"{settings.qdrant_collection_recipes}:lost",
                title=f"Qdrant «{settings.qdrant_collection_recipes}»: {len(lost)} рецептов помечены проиндексированными, но точки нет",
                evidence={"collection": settings.qdrant_collection_recipes,
                          "lost_count": len(lost), "sample": lost[:20]},
                suggested_fix={"action": "reindex_recipes", "note": "re-embed the missing recipes"},
            ))
    except Exception:
        pass
    return findings
