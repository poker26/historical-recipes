import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.recipe import Recipe, RecipeIngredient
from app.models.book import Book
from app.models.plant import Plant

router = APIRouter()


@router.get("/")
async def list_recipes(
    response: Response,
    category: str | None = None,
    book_id: uuid.UUID | None = None,
    domain: str | None = None,
    q: str | None = None,
    home_doable: bool | None = None,
    kind: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List recipes with search and filters.

    ``domain`` filters by the SOURCE book's domain (via the Book join), separating
    culinary recipes (``recipes``) from medicinal preparations harvested out of
    herbalism/fungi books (отвары/настои/сборы). Recipes carry no domain of their
    own — it is the book's — so this is the only way to split the two corpora.

    Pagination: pass ``limit``/``offset`` to fetch one page; the full filtered
    count is always returned in the ``X-Total-Count`` header. Omitting ``limit``
    returns every match (the historical behaviour the MCP tools rely on).
    """
    stmt = select(Recipe, Book.title, Book.author, Book.year).join(
        Book, Recipe.book_id == Book.id
    )

    if category:
        stmt = stmt.where(Recipe.category == category)
    if book_id:
        stmt = stmt.where(Recipe.book_id == book_id)
    if domain:
        stmt = stmt.where(Book.domain == domain.strip())
    if home_doable is not None:
        stmt = stmt.where(Recipe.home_doable.is_(home_doable))   # real, do-able recipes (vs junk)
    if kind:
        stmt = stmt.where(Recipe.recipe_kind == kind.strip())
    if q:
        # Two matchers OR'd together:
        #  1. ILIKE substring on name + original_text — the historical behaviour
        #     (prefixes, partial words, content matches).
        #  2. Russian stemmed full-text on the NAME — tolerant to declension and
        #     word order, so «Ночные стражи» (nominative) still finds «Водка ночных
        #     стражей» (genitive). Name-only keeps it cheap (no scan over the long
        #     original_text). Fixes named-recipe recall when the query's grammar
        #     differs from the stored title.
        pattern = f"%{q}%"
        name_tsv = func.to_tsvector("russian", func.coalesce(Recipe.name, ""))
        stmt = stmt.where(
            or_(
                Recipe.name.ilike(pattern),
                Recipe.original_text.ilike(pattern),
                name_tsv.op("@@")(func.plainto_tsquery("russian", q)),
            )
        )

    # Total matching rows (before pagination) → header, so the UI can render
    # "Page X of Y" without a second request.
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    stmt = stmt.order_by(Recipe.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "id": str(r.id),
            "book_id": str(r.book_id),
            "book_title": book_title,
            "book_author": book_author,
            "book_year": book_year,
            "name": r.name,
            "category": r.category,
            "recipe_kind": r.recipe_kind,
            "home_doable": r.home_doable,
            "step_by_step": (r.procedure_score or 0) >= 2,
            "original_text": r.original_text,
            "normalized_text": r.normalized_text,
            "year": r.year,
            "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
        }
        for (r, book_title, book_author, book_year) in rows
    ]


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get recipe details with ingredients."""
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ingredients))
        .where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    book = (
        await db.execute(select(Book).where(Book.id == recipe.book_id))
    ).scalar_one_or_none()

    # Resolve plant names for ingredients linked to the herbarium, so the recipe
    # page can render each as an active link to the plant monograph.
    plant_ids = {ing.plant_id for ing in recipe.ingredients if ing.plant_id}
    plant_names: dict[uuid.UUID, str] = {}
    if plant_ids:
        rows = (await db.execute(
            select(Plant.id, Plant.name).where(Plant.id.in_(plant_ids))
        )).all()
        plant_names = {pid: name for pid, name in rows}

    return {
        "id": str(recipe.id),
        "book_id": str(recipe.book_id),
        "book_title": book.title if book else None,
        "book_author": book.author if book else None,
        "book_year": book.year if book else None,
        "name": recipe.name,
        "category": recipe.category,
        "original_text": recipe.original_text,
        "normalized_text": recipe.normalized_text,
        "year": recipe.year,
        "indexed_at": recipe.indexed_at.isoformat() if recipe.indexed_at else None,
        "ingredients": [
            {
                "id": str(ing.id),
                "name": ing.name,
                "original_name": ing.original_name,
                "amount": ing.amount,
                "unit": ing.unit,
                "plant_id": str(ing.plant_id) if ing.plant_id else None,
                "plant_name": plant_names.get(ing.plant_id) if ing.plant_id else None,
            }
            for ing in recipe.ingredients
        ],
    }
