import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.plant import Compound, PlantCompound
from app.services.compound_matching import normalize_plant_compounds

router = APIRouter()


@router.post("/normalize")
async def normalize_compounds(db: AsyncSession = Depends(get_db)):
    """Map every PlantCompound.compound free-text string to the controlled
    compound vocabulary. Idempotent corpus-wide recompute — safe to re-run after
    a new phytochemistry reference grows the vocabulary (the compound analog of
    POST /api/plants/relink-recipes)."""
    result = await normalize_plant_compounds(db)
    return {"status": "completed", **result}


@router.get("")
async def list_compounds(db: AsyncSession = Depends(get_db)):
    """List the compound vocabulary with how many plant-facts each term normalizes."""
    counts = dict((cid, n) for cid, n in (await db.execute(
        select(PlantCompound.compound_id, func.count())
        .where(PlantCompound.compound_id.isnot(None))
        .group_by(PlantCompound.compound_id)
    )).all())
    rows = (await db.execute(select(Compound).order_by(Compound.name))).scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "name_latin": c.name_latin,
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "compound_class": c.compound_class,
            "synonyms": c.synonyms or [],
            "definition": c.definition,
            "linked_facts": counts.get(c.id, 0),
        }
        for c in rows
    ]
