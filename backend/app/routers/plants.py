import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.book import Book
from app.models.recipe import Recipe, RecipeIngredient
from app.models.plant import (
    Plant,
    MedicinalAction,
    PlantMedicinalUse,
    PlantCompound,
    PlantHarvest,
    PlantHabitat,
    PlantToxicity,
    PlantCulinaryUse,
    PlantBookMention,
)
from app.services.plant_matching import relink_recipe_ingredients, merge_plants_by_latin_key
from app.services.qdrant import delete_points

router = APIRouter()

QDRANT_PLANTS_COLLECTION = "plants_v2"


@router.post("/relink-recipes")
async def relink_recipes(db: AsyncSession = Depends(get_db)):
    """Backfill recipe↔plant links across the whole corpus.

    Recipe books processed before any herbalism book have NULL plant links
    (the plants table was empty at match time). This re-runs the normalized,
    alt-name-aware matcher over every ingredient now that plants exist.
    """
    result = await relink_recipe_ingredients(db)
    return {"status": "completed", **result}


@router.post("/dedupe-latin")
async def dedupe_latin(dry_run: bool = True, db: AsyncSession = Depends(get_db)):
    """Merge herbarium duplicates that share a latin binomial (genus + species).

    A plant can end up as several rows — a recipe book makes a stub, a determiner
    later adds the full monograph under "Genus species L." — whose latin names
    agree once the author citation and case are ignored. This folds each such
    group into its richest row, repointing all facts and recipe links.

    Defaults to ``dry_run`` (returns the plan, writes nothing) so the scale can
    be reviewed; pass ``?dry_run=false`` to execute. After a real merge the
    losing rows' ``plants_v2`` points are purged so search has no stale ghosts.
    """
    result = await merge_plants_by_latin_key(db, dry_run=dry_run)
    if not dry_run and result["deleted_qdrant_ids"]:
        await delete_points(QDRANT_PLANTS_COLLECTION, result["deleted_qdrant_ids"])
    return {"status": "completed", **result}


def _plant_summary(p: Plant, uses_count: int = 0) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "name_latin": p.name_latin,
        "names_historical": p.names_historical,
        "family": p.family,
        "family_latin": p.family_latin,
        "parts_used": p.parts_used,
        "is_toxic": p.is_toxic,
        "kingdom": p.kingdom,
        "uses_count": uses_count,
    }


@router.get("/")
async def list_plants(
    q: str | None = None,
    compound: str | None = None,
    action: str | None = None,
    indication: str | None = None,
    family: str | None = None,
    is_toxic: bool | None = None,
    edibility: str | None = None,
    edible: bool | None = None,
    kingdom: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List plants, optionally filtered by free text and/or structured facets.

    All filters combine with AND. ``compound``/``action``/``indication`` match
    against the plant's child fact rows via EXISTS (no row duplication). The
    ``action`` filter matches BOTH the normalized vocabulary (``action_id`` →
    MedicinalAction) and the verbatim ``action_raw``, since only ~44% of uses
    are normalized but ~97% carry a raw action term.
    """
    # Count medicinal uses per plant so the herbarium grid can show how rich
    # each card is without a second round-trip.
    uses_subq = (
        select(PlantMedicinalUse.plant_id, func.count().label("n"))
        .group_by(PlantMedicinalUse.plant_id)
        .subquery()
    )
    stmt = (
        select(Plant, func.coalesce(uses_subq.c.n, 0))
        .outerjoin(uses_subq, uses_subq.c.plant_id == Plant.id)
        .order_by(Plant.name)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Plant.name.ilike(like),
                Plant.name_latin.ilike(like),
                # ARRAY column: match any historical name
                func.array_to_string(Plant.names_historical, " ").ilike(like),
            )
        )
    if compound:
        like = f"%{compound.strip()}%"
        stmt = stmt.where(
            Plant.compounds.any(
                or_(
                    PlantCompound.compound.ilike(like),
                    PlantCompound.compound_group.ilike(like),
                )
            )
        )
    if action:
        like = f"%{action.strip()}%"
        action_ids = select(MedicinalAction.id).where(
            or_(MedicinalAction.name.ilike(like), MedicinalAction.name_modern.ilike(like))
        )
        stmt = stmt.where(
            Plant.medicinal_uses.any(
                or_(
                    PlantMedicinalUse.action_raw.ilike(like),
                    PlantMedicinalUse.action_id.in_(action_ids),
                )
            )
        )
    if indication:
        like = f"%{indication.strip()}%"
        stmt = stmt.where(
            Plant.medicinal_uses.any(PlantMedicinalUse.indications.ilike(like))
        )
    if family:
        like = f"%{family.strip()}%"
        stmt = stmt.where(or_(Plant.family.ilike(like), Plant.family_latin.ilike(like)))
    if is_toxic is not None:
        stmt = stmt.where(Plant.is_toxic.is_(is_toxic))
    if kingdom:
        # Exact match on the kingdom tag (растение | гриб). Omit to get both; the
        # catalogue passes kingdom=растение to keep the plant view fungi-free.
        stmt = stmt.where(Plant.kingdom == kingdom.strip())
    if edibility:
        like = f"%{edibility.strip()}%"
        stmt = stmt.where(Plant.culinary_uses.any(PlantCulinaryUse.edibility.ilike(like)))
    if edible is not None:
        # "edible" = has any culinary fact flagged съедобно / условно-съедобно.
        edible_pred = Plant.culinary_uses.any(
            PlantCulinaryUse.edibility.in_(["съедобно", "условно-съедобно"])
        )
        stmt = stmt.where(edible_pred if edible else ~edible_pred)

    rows = (await db.execute(stmt)).all()
    return [_plant_summary(p, n) for p, n in rows]


@router.get("/facets")
async def plant_facets(db: AsyncSession = Depends(get_db)):
    """Distinct filter options for the herbarium UI, each with a plant count.

    ``compound_groups`` are the normalized constituent groups; ``actions`` are
    the normalized medicinal-action vocabulary terms actually in use. Counts are
    distinct plants, so they read as "N plants have this".
    """
    group_count = func.count(func.distinct(PlantCompound.plant_id))
    groups = (await db.execute(
        select(PlantCompound.compound_group, group_count)
        .where(PlantCompound.compound_group.isnot(None))
        .group_by(PlantCompound.compound_group)
        .order_by(group_count.desc())
    )).all()

    action_count = func.count(func.distinct(PlantMedicinalUse.plant_id))
    actions = (await db.execute(
        select(MedicinalAction.name, action_count)
        .join(PlantMedicinalUse, PlantMedicinalUse.action_id == MedicinalAction.id)
        .group_by(MedicinalAction.name)
        .order_by(action_count.desc())
    )).all()

    edib_count = func.count(func.distinct(PlantCulinaryUse.plant_id))
    edibilities = (await db.execute(
        select(PlantCulinaryUse.edibility, edib_count)
        .where(PlantCulinaryUse.edibility.isnot(None))
        .group_by(PlantCulinaryUse.edibility)
        .order_by(edib_count.desc())
    )).all()

    kingdom_count = func.count(Plant.id)
    kingdoms = (await db.execute(
        select(Plant.kingdom, kingdom_count)
        .group_by(Plant.kingdom)
        .order_by(kingdom_count.desc())
    )).all()

    return {
        "compound_groups": [{"value": g, "count": n} for g, n in groups],
        "actions": [{"value": a, "count": n} for a, n in actions],
        "edibility": [{"value": e, "count": n} for e, n in edibilities],
        "kingdom": [{"value": k, "count": n} for k, n in kingdoms],
    }


def _book_title_map(books: list[Book]) -> dict[str, str]:
    return {str(b.id): b.title for b in books}


@router.get("/{plant_id}")
async def get_plant(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Full plant monograph: identity + all source-layered facts."""
    stmt = (
        select(Plant)
        .where(Plant.id == plant_id)
        .options(
            selectinload(Plant.medicinal_uses).selectinload(PlantMedicinalUse.action),
            selectinload(Plant.compounds),
            selectinload(Plant.harvests),
            selectinload(Plant.habitats),
            selectinload(Plant.toxicities),
            selectinload(Plant.culinary_uses),
            selectinload(Plant.mentions),
        )
    )
    plant = (await db.execute(stmt)).scalar_one_or_none()
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")

    # Collect every source_book_id referenced across the child rows, then resolve
    # titles in one query for human-readable source attribution.
    book_ids: set[uuid.UUID] = set()
    for u in plant.medicinal_uses:
        if u.source_book_id:
            book_ids.add(u.source_book_id)
    for c in plant.compounds:
        if c.source_book_id:
            book_ids.add(c.source_book_id)
    for h in plant.harvests:
        if h.source_book_id:
            book_ids.add(h.source_book_id)
    for h in plant.habitats:
        if h.source_book_id:
            book_ids.add(h.source_book_id)
    for t in plant.toxicities:
        if t.source_book_id:
            book_ids.add(t.source_book_id)
    for cu in plant.culinary_uses:
        if cu.source_book_id:
            book_ids.add(cu.source_book_id)
    for m in plant.mentions:
        book_ids.add(m.book_id)

    titles: dict[str, str] = {}
    if book_ids:
        books = (await db.execute(select(Book).where(Book.id.in_(book_ids)))).scalars().all()
        titles = _book_title_map(books)

    def src(book_id) -> str | None:
        return titles.get(str(book_id)) if book_id else None

    # Cross-domain link: recipes whose ingredients resolved to this plant.
    recipe_rows = (await db.execute(
        select(Recipe.id, Recipe.name, Recipe.category, Book.title, Book.year)
        .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
        .join(Book, Recipe.book_id == Book.id)
        .where(RecipeIngredient.plant_id == plant_id)
        .distinct()
        .order_by(Recipe.name)
    )).all()
    recipes = [
        {
            "id": str(rid),
            "name": rname,
            "category": rcat,
            "book": btitle,
            "year": byear,
        }
        for (rid, rname, rcat, btitle, byear) in recipe_rows
    ]

    return {
        "id": str(plant.id),
        "name": plant.name,
        "name_latin": plant.name_latin,
        "names_historical": plant.names_historical,
        "family": plant.family,
        "family_latin": plant.family_latin,
        "description": plant.description,
        "parts_used": plant.parts_used,
        "is_toxic": plant.is_toxic,
        "kingdom": plant.kingdom,
        "medicinal_uses": [
            {
                "id": str(u.id),
                "part": u.part,
                "action": u.action.name if u.action else u.action_raw,
                "action_system": u.action.system if u.action else None,
                "indications": u.indications,
                "preparation": u.preparation,
                "dosage": u.dosage,
                "contraindications": u.contraindications,
                "original_text": u.original_text,
                "confidence": u.confidence,
                "source": src(u.source_book_id),
            }
            for u in plant.medicinal_uses
        ],
        "compounds": [
            {
                "id": str(c.id),
                "compound": c.compound,
                "compound_group": c.compound_group,
                "part": c.part,
                "notes": c.notes,
                "source": src(c.source_book_id),
            }
            for c in plant.compounds
        ],
        "harvests": [
            {
                "id": str(h.id),
                "part": h.part,
                "season": h.season,
                "method": h.method,
                "original_text": h.original_text,
                "source": src(h.source_book_id),
            }
            for h in plant.harvests
        ],
        "habitats": [
            {
                "id": str(h.id),
                "region": h.region,
                "biotope": h.biotope,
                "status": h.status,
                "original_text": h.original_text,
                "source": src(h.source_book_id),
            }
            for h in plant.habitats
        ],
        "toxicities": [
            {
                "id": str(t.id),
                "toxic_parts": t.toxic_parts,
                "symptoms": t.symptoms,
                "antidote": t.antidote,
                "severity": t.severity,
                "original_text": t.original_text,
                "source": src(t.source_book_id),
            }
            for t in plant.toxicities
        ],
        "culinary_uses": [
            {
                "id": str(cu.id),
                "part": cu.part,
                "edibility": cu.edibility,
                "preparation": cu.preparation,
                "use": cu.use,
                "season": cu.season,
                "caution": cu.caution,
                "original_text": cu.original_text,
                "confidence": cu.confidence,
                "source": src(cu.source_book_id),
            }
            for cu in plant.culinary_uses
        ],
        "mentions": [
            {
                "id": str(m.id),
                "book": titles.get(str(m.book_id)),
                "original_name": m.original_name,
                "page_number": m.page_number,
            }
            for m in plant.mentions
        ],
        "recipes": recipes,
    }
