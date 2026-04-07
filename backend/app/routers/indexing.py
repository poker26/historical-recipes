import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


class EmbedRequest(BaseModel):
    text: str


@router.post("/embed")
async def create_embedding(request: EmbedRequest):
    """Create dense + sparse vectors for text via BGE-M3."""
    # TODO: implement
    return {}


@router.post("/book/{book_id}")
async def index_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Index all recipes/plants from a book into Qdrant."""
    # TODO: implement
    return {}


@router.delete("/book/{book_id}")
async def delete_book_vectors(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Remove all vectors for a book from Qdrant."""
    # TODO: implement
    return {}
