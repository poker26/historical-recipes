import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_plants(q: str | None = None, db: AsyncSession = Depends(get_db)):
    """List plants with search."""
    # TODO: implement
    return []


@router.get("/{plant_id}")
async def get_plant(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get plant card with properties and recipe references."""
    # TODO: implement
    return {}


@router.post("/")
async def create_plant(db: AsyncSession = Depends(get_db)):
    """Add a new plant to the catalog."""
    # TODO: implement
    return {}


@router.put("/{plant_id}")
async def update_plant(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Edit plant details."""
    # TODO: implement
    return {}


@router.get("/{plant_id}/recipes")
async def plant_recipes(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List recipes containing this plant."""
    # TODO: implement
    return []


@router.get("/{plant_id}/compatible")
async def compatible_plants(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List compatible plants."""
    # TODO: implement
    return []


@router.post("/parse")
async def parse_plants(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Extract plant information from herbalism book chunks."""
    # TODO: implement
    return {}
