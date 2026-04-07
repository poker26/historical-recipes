from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_terms(category: str | None = None, db: AsyncSession = Depends(get_db)):
    """List dictionary terms with optional category filter."""
    # TODO: implement
    return []


@router.post("/")
async def create_term(db: AsyncSession = Depends(get_db)):
    """Add a dictionary term."""
    # TODO: implement
    return {}


@router.get("/lookup")
async def lookup_term(term: str, db: AsyncSession = Depends(get_db)):
    """Look up a term in dictionaries."""
    # TODO: implement
    return {}
