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
    PlantMedicinalUse,
    PlantCompound,
    PlantHarvest,
    PlantHabitat,
    PlantToxicity,
    PlantBookMention,
)
from app.services.plant_matching import relink_recipe_ingredients

router = APIRouter()


@router.post("/relink-recipes")
async def relink_recipes(db: AsyncSession = Depends(get_db)):
    """Backfill recipe↔plant links across the whole corpus.

    Recipe books processed before any herbalism book have NULL plant links
    (the plants table was empty at match time). This re-runs the normalized,
    alt-name-aware matcher over every ingredient now that plants exist.
    """
    result = await relink_recipe_ingredients(db)
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
        "uses_count": uses_count,
    }


@router.get("/")
async def list_plants(q: str | None = None, db: AsyncSession = Depends(get_db)):
    """List plants with optional fuzzy search over name / latin / historical names."""
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
    rows = (await db.execute(stmt)).all()
    return [_plant_summary(p, n) for p, n in rows]


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
