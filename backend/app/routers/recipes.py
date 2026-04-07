import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_recipes(
    category: str | None = None,
    book_id: uuid.UUID | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List recipes with search and filters."""
    # TODO: implement
    return []


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get recipe details with ingredients."""
    # TODO: implement
    return {}


@router.post("/parse")
async def parse_recipes(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Parse recipes from book chunks."""
    # TODO: implement
    return {}


@router.put("/{recipe_id}")
async def update_recipe(recipe_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Manually edit a recipe."""
    # TODO: implement
    return {}
