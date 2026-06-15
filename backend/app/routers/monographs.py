"""Admin surface for the Layer-2 reader-monograph review loop (RFC-reader-monograph
§8). The first batch is human-reviewed before it serves: a generated monograph is
stored with `reviewed=false` (not served) until approved here. `?view=field` only
serves `reviewed=true` rows; everything else falls back to live aggregation.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.reader_monograph import PlantReaderMonograph

router = APIRouter()


def _has_p0(findings) -> bool:
    return any((f or {}).get("severity") == "P0" for f in (findings or []))


@router.get("/pending")
async def pending(limit: int = Query(50, le=500), db: AsyncSession = Depends(get_db)):
    """Generated-but-unpublished monographs awaiting review, P0-flagged first."""
    rows = (await db.execute(
        select(PlantReaderMonograph.plant_id, PlantReaderMonograph.monograph,
               PlantReaderMonograph.gate_findings, PlantReaderMonograph.model,
               PlantReaderMonograph.updated_at)
        .where(PlantReaderMonograph.reviewed.is_(False))
        .limit(limit))).all()
    items = [{
        "plant_id": str(pid), "name": (mono or {}).get("name"),
        "has_p0": _has_p0(findings), "gate_findings": findings or [],
        "model": model, "updated_at": ts.isoformat() if ts else None,
    } for (pid, mono, findings, model, ts) in rows]
    items.sort(key=lambda x: (not x["has_p0"],))  # P0 first
    return {"count": len(items), "items": items}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Counts: total generated, published, pending, P0-blocked."""
    total = (await db.execute(select(func.count()).select_from(PlantReaderMonograph))).scalar()
    published = (await db.execute(select(func.count()).select_from(PlantReaderMonograph)
                                  .where(PlantReaderMonograph.reviewed.is_(True)))).scalar()
    blocked = (await db.execute(text(
        "SELECT count(*) FROM plant_reader_monograph WHERE reviewed=false "
        "AND gate_findings::text LIKE '%\"P0\"%'"))).scalar()
    return {"total": total, "published": published,
            "pending": total - published, "p0_blocked": blocked}


@router.get("/{plant_id}")
async def preview(plant_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Full stored monograph + gate findings — for human review BEFORE publishing
    (returns it whether or not it's reviewed; `?view=field` only serves reviewed)."""
    row = await db.get(PlantReaderMonograph, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no monograph generated for this plant")
    return {"plant_id": str(plant_id), "reviewed": row.reviewed,
            "gate_findings": row.gate_findings or [], "has_p0": _has_p0(row.gate_findings),
            "generated_from_hash": row.generated_from_hash, "model": row.model,
            "monograph": row.monograph}


@router.post("/{plant_id}/approve")
async def approve(plant_id: uuid.UUID, force: bool = Query(False, description="publish despite P0"),
                  db: AsyncSession = Depends(get_db)):
    """Publish: set reviewed=true (and sync the flag inside the stored JSON). Refuses
    a P0-blocked monograph unless ?force=true (P0 = ungrounded/identity-conflict)."""
    row = await db.get(PlantReaderMonograph, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no monograph generated for this plant")
    if _has_p0(row.gate_findings) and not force:
        raise HTTPException(status_code=409,
                            detail="P0 publish-gate finding — fix or pass ?force=true")
    await db.execute(text(
        "UPDATE plant_reader_monograph SET reviewed=true, "
        "monograph=jsonb_set(monograph,'{reviewed}','true'::jsonb), updated_at=now() "
        "WHERE plant_id=:id"), {"id": str(plant_id)})
    await db.commit()
    return {"status": "published", "plant_id": str(plant_id)}


@router.post("/{plant_id}/reject")
async def reject(plant_id: uuid.UUID, delete: bool = Query(False, description="drop the row entirely"),
                 db: AsyncSession = Depends(get_db)):
    """Unpublish (reviewed=false) or, with ?delete=true, drop the row so it
    regenerates fresh on the next batch."""
    row = await db.get(PlantReaderMonograph, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no monograph generated for this plant")
    if delete:
        await db.delete(row)
    else:
        row.reviewed = False
        await db.execute(text(
            "UPDATE plant_reader_monograph SET monograph=jsonb_set(monograph,'{reviewed}','false'::jsonb) "
            "WHERE plant_id=:id"), {"id": str(plant_id)})
    await db.commit()
    return {"status": "deleted" if delete else "unpublished", "plant_id": str(plant_id)}
