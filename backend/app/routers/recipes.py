import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.recipe import Recipe, RecipeIngredient
from app.models.book import Book

router = APIRouter()


@router.get("/")
async def list_recipes(
    category: str | None = None,
    book_id: uuid.UUID | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List recipes with search and filters."""
    stmt = select(Recipe).join(Book, Recipe.book_id == Book.id)

    if category:
        stmt = stmt.where(Recipe.category == category)
    if book_id:
        stmt = stmt.where(Recipe.book_id == book_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(Recipe.name.ilike(pattern), Recipe.original_text.ilike(pattern))
        )

    stmt = stmt.order_by(Recipe.created_at.desc())
    result = await db.execute(stmt)
    recipes = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "book_id": str(r.book_id),
            "name": r.name,
            "category": r.category,
            "original_text": r.original_text,
            "normalized_text": r.normalized_text,
            "year": r.year,
            "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
        }
        for r in recipes
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

    return {
        "id": str(recipe.id),
        "book_id": str(recipe.book_id),
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
            }
            for ing in recipe.ingredients
        ],
    }
