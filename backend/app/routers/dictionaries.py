import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dictionary import DictionaryTerm
from app.schemas.dictionary import DictionaryTermCreate, DictionaryTermUpdate, DictionaryTermOut

router = APIRouter()


@router.get("/", response_model=list[DictionaryTermOut])
async def list_terms(
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List dictionary terms with optional category filter."""
    query = select(DictionaryTerm).order_by(DictionaryTerm.category, DictionaryTerm.term_old)
    if category:
        query = query.where(DictionaryTerm.category == category)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """List all dictionary categories with counts."""
    from sqlalchemy import func, distinct
    result = await db.execute(
        select(DictionaryTerm.category, func.count(DictionaryTerm.id))
        .group_by(DictionaryTerm.category)
        .order_by(DictionaryTerm.category)
    )
    return [{"category": row[0], "count": row[1]} for row in result.all()]


@router.get("/lookup", response_model=list[DictionaryTermOut])
async def lookup_term(
    term: str = Query(..., min_length=1),
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Look up a term in dictionaries (case-insensitive partial match)."""
    query = select(DictionaryTerm).where(
        DictionaryTerm.term_old.ilike(f"%{term}%")
    )
    if category:
        query = query.where(DictionaryTerm.category == category)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=DictionaryTermOut)
async def create_term(
    body: DictionaryTermCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a dictionary term."""
    term = DictionaryTerm(**body.model_dump())
    db.add(term)
    await db.commit()
    await db.refresh(term)
    return term


@router.post("/bulk", response_model=list[DictionaryTermOut])
async def create_terms_bulk(
    terms: list[DictionaryTermCreate],
    db: AsyncSession = Depends(get_db),
):
    """Add multiple dictionary terms at once."""
    db_terms = [DictionaryTerm(**t.model_dump()) for t in terms]
    db.add_all(db_terms)
    await db.commit()
    for t in db_terms:
        await db.refresh(t)
    return db_terms


@router.put("/{term_id}", response_model=DictionaryTermOut)
async def update_term(
    term_id: uuid.UUID,
    body: DictionaryTermUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a dictionary term."""
    result = await db.execute(select(DictionaryTerm).where(DictionaryTerm.id == term_id))
    term = result.scalar_one_or_none()
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(term, field, value)

    await db.commit()
    await db.refresh(term)
    return term


@router.delete("/{term_id}")
async def delete_term(term_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a dictionary term."""
    result = await db.execute(select(DictionaryTerm).where(DictionaryTerm.id == term_id))
    term = result.scalar_one_or_none()
    if not term:
        raise HTTPException(status_code=404, detail="Term not found")

    await db.delete(term)
    await db.commit()
    return {"status": "deleted"}
