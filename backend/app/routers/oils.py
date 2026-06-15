import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.plant import (
    EssentialOil, EssentialOilUse, Plant, MedicinalAction, Indication,
)
from app.services.aroma_matching import normalize_oil_uses

router = APIRouter()


@router.post("/normalize")
async def normalize_oils(db: AsyncSession = Depends(get_db)):
    """Map every EssentialOilUse action/indication free-text string to the shared
    medical vocabularies. Idempotent corpus-wide recompute — safe to re-run after
    a new aromatherapy book or after the medical vocabulary grows (the oil analog
    of POST /api/compounds/normalize)."""
    result = await normalize_oil_uses(db)
    return {"status": "completed", **result}


@router.get("")
async def list_oils(
    q: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List named essential oils with their source plant and a use count."""
    use_counts = dict((oid, n) for oid, n in (await db.execute(
        select(EssentialOilUse.oil_id, func.count())
        .group_by(EssentialOilUse.oil_id)
    )).all())

    stmt = select(EssentialOil, Plant).join(
        Plant, EssentialOil.plant_id == Plant.id, isouter=True)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            EssentialOil.name.ilike(like),
            EssentialOil.name_latin.ilike(like),
            EssentialOil.source_plant_raw.ilike(like),
        ))
    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(EssentialOil.name).limit(limit).offset(offset))).all()
    items = [
        {
            "id": str(oil.id),
            "name": oil.name,
            "name_latin": oil.name_latin,
            "plant_id": str(oil.plant_id) if oil.plant_id else None,
            "plant_name": plant.name if plant else (oil.source_plant_raw or None),
            "plant_name_latin": plant.name_latin if plant else None,
            "part": oil.part,
            "extraction": oil.extraction,
            "aroma_profile": oil.aroma_profile,
            "uses_count": use_counts.get(oil.id, 0),
        }
        for oil, plant in rows
    ]
    return {"items": items, "total": total}


@router.get("/for-condition")
async def oils_for_condition(
    condition: str = Query(..., min_length=2),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Reverse lookup: essential oils used FOR a condition. The oil analog of
    plants_for_condition — resolves ``condition`` across BOTH medical axes (the
    indication vocabulary with the archaic→modern bridge AND the action
    vocabulary) and unions the oils whose use-facts match on either axis,
    keeping the verbatim free-text fallback for not-yet-normalized uses.

    Returns {condition, count, oils:[...]} where each oil is a card (same shape
    as list_oils) plus ``matched_uses`` — how many of that oil's use-facts hit
    the condition (the strength of the match)."""
    like = f"%{condition.strip()}%"

    # Action axis: vocabulary terms (name / modern / synonyms) + their hierarchy
    # descendants, plus the verbatim action_raw fallback.
    matched_actions = (await db.execute(
        select(MedicinalAction.id).where(
            or_(
                MedicinalAction.name.ilike(like),
                MedicinalAction.name_modern.ilike(like),
                func.array_to_string(MedicinalAction.synonyms, " ").ilike(like),
            )
        )
    )).scalars().all()
    action_id_set = set(matched_actions)
    if action_id_set:
        descendants = (await db.execute(
            select(MedicinalAction.id).where(MedicinalAction.parent_id.in_(action_id_set))
        )).scalars().all()
        action_id_set.update(descendants)

    # Indication axis: vocabulary concepts (name / modern / synonyms / archaic) +
    # their children, plus the verbatim indications free-text fallback.
    matched_inds = (await db.execute(
        select(Indication.id).where(
            or_(
                Indication.name.ilike(like),
                Indication.name_modern.ilike(like),
                func.array_to_string(Indication.synonyms, " ").ilike(like),
                func.array_to_string(Indication.archaic, " ").ilike(like),
            )
        )
    )).scalars().all()
    concept_ids = set(matched_inds)
    if concept_ids:
        children = (await db.execute(
            select(Indication.id).where(Indication.parent_id.in_(concept_ids))
        )).scalars().all()
        concept_ids.update(children)

    preds = [
        EssentialOilUse.action_raw.ilike(like),
        EssentialOilUse.indications.ilike(like),
    ]
    if action_id_set:
        preds.append(EssentialOilUse.action_id.in_(action_id_set))
    preds += [EssentialOilUse.indication_ids.any(cid) for cid in concept_ids]

    # Count matching uses per oil, most-relevant oil first.
    match_counts = (await db.execute(
        select(EssentialOilUse.oil_id, func.count())
        .where(or_(*preds))
        .group_by(EssentialOilUse.oil_id)
    )).all()
    if not match_counts:
        return {"condition": condition, "count": 0, "oils": []}

    matched = dict(match_counts)
    oil_ids = list(matched.keys())

    # Total use counts (for the card) and the oil+plant rows.
    use_counts = dict((oid, n) for oid, n in (await db.execute(
        select(EssentialOilUse.oil_id, func.count())
        .where(EssentialOilUse.oil_id.in_(oil_ids))
        .group_by(EssentialOilUse.oil_id)
    )).all())
    rows = (await db.execute(
        select(EssentialOil, Plant)
        .join(Plant, EssentialOil.plant_id == Plant.id, isouter=True)
        .where(EssentialOil.id.in_(oil_ids))
    )).all()

    oils = [
        {
            "id": str(oil.id),
            "name": oil.name,
            "name_latin": oil.name_latin,
            "plant_id": str(oil.plant_id) if oil.plant_id else None,
            "plant_name": plant.name if plant else (oil.source_plant_raw or None),
            "plant_name_latin": plant.name_latin if plant else None,
            "part": oil.part,
            "extraction": oil.extraction,
            "aroma_profile": oil.aroma_profile,
            "uses_count": use_counts.get(oil.id, 0),
            "matched_uses": matched.get(oil.id, 0),
        }
        for oil, plant in rows
    ]
    oils.sort(key=lambda o: o["matched_uses"], reverse=True)
    oils = oils[:limit]
    return {"condition": condition, "count": len(oils), "oils": oils}


@router.get("/{oil_id}")
async def get_oil(oil_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """A single essential-oil monograph: its source plant (herbarium bridge), its
    node in the chemistry vocabulary, and its aromatherapy use-facts with the
    normalized action / indication names resolved."""
    oil = (await db.execute(
        select(EssentialOil).where(EssentialOil.id == oil_id))).scalar_one_or_none()
    if oil is None:
        raise HTTPException(status_code=404, detail="Essential oil not found")

    plant = None
    if oil.plant_id:
        p = (await db.execute(select(Plant).where(Plant.id == oil.plant_id))).scalar_one_or_none()
        if p is not None:
            plant = {"id": str(p.id), "name": p.name, "name_latin": p.name_latin,
                     "photo_url": p.photo_url}

    use_rows = (await db.execute(
        select(EssentialOilUse).where(EssentialOilUse.oil_id == oil.id))).scalars().all()

    # Resolve normalized action names and indication concept names in one pass.
    action_ids = {u.action_id for u in use_rows if u.action_id}
    ind_ids = {iid for u in use_rows for iid in (u.indication_ids or [])}
    action_names = {}
    if action_ids:
        action_names = dict((a.id, a.name) for a in (await db.execute(
            select(MedicinalAction).where(MedicinalAction.id.in_(action_ids)))).scalars().all())
    ind_names = {}
    if ind_ids:
        ind_names = dict((i.id, i.name) for i in (await db.execute(
            select(Indication).where(Indication.id.in_(ind_ids)))).scalars().all())

    uses = [
        {
            "id": str(u.id),
            "action": action_names.get(u.action_id) or u.action_raw,
            "action_raw": u.action_raw,
            "indications": u.indications,
            "indication_concepts": [ind_names[i] for i in (u.indication_ids or []) if i in ind_names],
            "application": u.application,
            "dosage": u.dosage,
            "contraindications": u.contraindications,
            "original_text": u.original_text,
        }
        for u in use_rows
    ]

    return {
        "id": str(oil.id),
        "name": oil.name,
        "name_latin": oil.name_latin,
        "synonyms": oil.synonyms or [],
        "plant": plant,
        "source_plant_raw": oil.source_plant_raw,
        "compound_id": str(oil.compound_id) if oil.compound_id else None,
        "part": oil.part,
        "extraction": oil.extraction,
        "aroma_profile": oil.aroma_profile,
        "description": oil.description,
        "original_text": oil.original_text,
        "uses": uses,
    }
