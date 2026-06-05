import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.plant import Compound, PlantCompound, Plant
from app.services.compound_matching import normalize_plant_compounds
from app.services.vocab_dedup import dedup_compounds
from app.services.associations import compound_associations

router = APIRouter()


@router.post("/normalize")
async def normalize_compounds(db: AsyncSession = Depends(get_db)):
    """Map every PlantCompound.compound free-text string to the controlled
    compound vocabulary. Idempotent corpus-wide recompute — safe to re-run after
    a new phytochemistry reference grows the vocabulary (the compound analog of
    POST /api/plants/relink-recipes)."""
    result = await normalize_plant_compounds(db)
    return {"status": "completed", **result}


@router.post("/dedup")
async def dedup_compound_vocab(apply: bool = False, db: AsyncSession = Depends(get_db)):
    """Merge near-duplicate COMPOUND concepts (spelling twins, a row whose headword IS
    another row's Latin name …). The chemistry analog of POST /api/medical/dedup, over
    the SCALAR compound_id link (a plain UPDATE, not an array rewrite); the secondary
    canonical name here is name_latin, and there's no archaic bridge. Review-first:
    ``apply=false`` (default) returns the proposed merges + clusters skipped for
    hierarchy reasons WITHOUT writing; ``apply=true`` folds each duplicate's surface
    forms into the canonical concept (recall preserved), repoints every
    PlantCompound.compound_id + child parent_id, deletes the duplicates, commits.
    Idempotent."""
    result = await dedup_compounds(db, apply=apply)
    return {"status": "applied" if apply else "dry_run", **result}


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


@router.get("/{compound_id}/associations")
async def compound_assoc(
    compound_id: uuid.UUID,
    axis: str = "indication",
    limit: int = 20,
    min_support: int = 2,
    db: AsyncSession = Depends(get_db),
):
    """Hypothesis-generating co-occurrence: the INDICATIONS (``axis=indication``) or
    ACTIONS (``axis=action``) that plants containing this compound (and its hierarchy
    descendants) tend to be used for, ranked by a one-sided hypergeometric p-value
    (NOT raw lift — small compounds inflate lift on coincidences). Each row carries
    lift, support counts and the supporting plants so the association is auditable.
    This is an association VIA the bridging plant, never a causal claim that the
    compound treats the condition. ``axis=action`` is the more robust default-quality
    view (broader categories → more support per cell)."""
    return await compound_associations(db, compound_id, axis=axis, limit=limit, min_support=min_support)


@router.get("/{compound_id}")
async def get_compound(compound_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """A single vocabulary term plus the plants whose composition normalizes to it
    (reverse PlantCompound.compound_id link) and its place in the hierarchy."""
    c = (await db.execute(select(Compound).where(Compound.id == compound_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Compound not found")

    parent = None
    if c.parent_id:
        p = (await db.execute(select(Compound).where(Compound.id == c.parent_id))).scalar_one_or_none()
        if p is not None:
            parent = {"id": str(p.id), "name": p.name}

    children = (await db.execute(
        select(Compound).where(Compound.parent_id == c.id).order_by(Compound.name)
    )).scalars().all()

    # Plants that contain this compound (reverse link). One row per plant-fact;
    # group by plant so a plant listed for several parts appears once with its parts.
    fact_rows = (await db.execute(
        select(PlantCompound, Plant)
        .join(Plant, PlantCompound.plant_id == Plant.id)
        .where(PlantCompound.compound_id == c.id)
        .order_by(Plant.name)
    )).all()
    plants: dict[str, dict] = {}
    for pc, plant in fact_rows:
        pid = str(plant.id)
        entry = plants.setdefault(pid, {
            "id": pid,
            "name": plant.name,
            "name_latin": plant.name_latin,
            "parts": [],
            "raw_names": [],
        })
        if pc.part and pc.part not in entry["parts"]:
            entry["parts"].append(pc.part)
        if pc.compound and pc.compound not in entry["raw_names"]:
            entry["raw_names"].append(pc.compound)

    return {
        "id": str(c.id),
        "name": c.name,
        "name_latin": c.name_latin,
        "parent": parent,
        "parent_id": str(c.parent_id) if c.parent_id else None,
        "compound_class": c.compound_class,
        "synonyms": c.synonyms or [],
        "definition": c.definition,
        "original_text": c.original_text,
        "children": [{"id": str(ch.id), "name": ch.name} for ch in children],
        "plants": list(plants.values()),
        "linked_facts": sum(len(p["raw_names"]) for p in plants.values()),
    }
