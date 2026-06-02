import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.book import Book, ProcessingLog
from app.models.recipe import Recipe
from app.services import embedder, qdrant as qdrant_svc

router = APIRouter()


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    dense_dim: int
    sparse_tokens: int


@router.post("/embed", response_model=EmbedResponse)
async def create_embedding(request: EmbedRequest):
    """Create dense + sparse vectors for text via BGE-M3."""
    vectors = await embedder.create_embedding(request.text)
    dense = vectors.get("dense", [])
    sparse = vectors.get("sparse", {})
    return EmbedResponse(
        dense_dim=len(dense),
        sparse_tokens=len(sparse.get("indices", [])),
    )


@router.post("/book/{book_id}")
async def index_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Index all recipes from a book into Qdrant.

    Generates embeddings for each recipe and upserts into the recipes collection.
    """
    # Check book exists
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Get unindexed recipes for this book
    result = await db.execute(
        select(Recipe)
        .where(Recipe.book_id == book_id, Recipe.qdrant_point_id.is_(None))
    )
    recipes = result.scalars().all()

    if not recipes:
        return {"status": "no_recipes", "indexed": 0}

    # Ensure collection exists
    collection = settings.qdrant_collection_recipes
    await qdrant_svc.ensure_collection(collection)

    # Log start
    log = ProcessingLog(book_id=book_id, step="index", status="started",
                        details={"recipes_count": len(recipes)})
    db.add(log)
    await db.flush()

    indexed = 0
    for recipe in recipes:
        # Use normalized text if available, otherwise original
        text = recipe.normalized_text or recipe.original_text
        if not text:
            continue

        # Create embedding
        vectors = await embedder.create_embedding(text)
        point_id = str(uuid.uuid4())

        # Upsert to Qdrant
        await qdrant_svc.upsert_points(collection, [{
            "id": point_id,
            "dense": vectors.get("dense", []),
            "sparse": vectors.get("sparse"),
            "payload": {
                "recipe_id": str(recipe.id),
                "recipe_name": recipe.name,
                "category": recipe.category,
                "source_book": str(book_id),
                "book_title": book.title,
                "year": recipe.year or book.year,
                "content": text[:2000],  # truncate for payload
            },
        }])

        # Update recipe with Qdrant reference
        recipe.qdrant_point_id = point_id
        recipe.qdrant_collection = collection
        from datetime import datetime, timezone
        recipe.indexed_at = datetime.now(timezone.utc)
        indexed += 1

    # Update book status
    book.status = "indexed"

    # Log completion
    log_done = ProcessingLog(book_id=book_id, step="index", status="completed",
                             details={"indexed": indexed})
    db.add(log_done)
    await db.commit()

    return {"status": "indexed", "indexed": indexed}


@router.delete("/book/{book_id}")
async def delete_book_vectors(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Remove all vectors for a book from Qdrant."""
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Only touch collections that actually exist — on the shared Qdrant the
    # herbalism collection may be absent, and deleting from a missing
    # collection 404s (previously surfaced as a 500 for the whole request).
    existing = set(await qdrant_svc.list_collections())
    for collection in (
        settings.qdrant_collection_recipes,
        settings.qdrant_collection_herbalism,
    ):
        if collection in existing:
            await qdrant_svc.delete_by_filter(collection, "source_book", str(book_id))

    # Section passages are keyed by the "book_id" payload field (see
    # _index_sections), so clean them up with the matching filter.
    if settings.qdrant_collection_sections in existing:
        await qdrant_svc.delete_by_filter(
            settings.qdrant_collection_sections, "book_id", str(book_id))

    # Clear Qdrant references in recipes
    await db.execute(
        update(Recipe)
        .where(Recipe.book_id == book_id)
        .values(qdrant_point_id=None, qdrant_collection=None, indexed_at=None)
    )

    # Log
    log = ProcessingLog(book_id=book_id, step="deindex", status="completed")
    db.add(log)
    await db.commit()

    return {"status": "deleted"}
