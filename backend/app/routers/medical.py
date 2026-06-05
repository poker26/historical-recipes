"""`/api/medical` — the medical-normalizer's corpus operations + read surface.

Two corpus-wide maintenance ops (mirroring `/api/compounds`):

- ``POST /build-vocab`` (Phase A) canonicalizes the distinct ``action_raw`` and
  ``indications`` atoms the corpus has accumulated into the ``MedicinalAction`` /
  ``Indication`` vocabularies, with the archaic→modern bridge.
- ``POST /normalize`` (Phase B) re-derives ``action_id`` + ``indication_ids`` on
  every ``PlantMedicinalUse`` from those vocabularies. Idempotent full recompute.

Plus a read surface over the vocabularies for the admin UI / MCP layer.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.plant import MedicinalAction, Indication, PlantMedicinalUse, Plant
from app.services.medical_matching import normalize_medical_uses
from app.services.medical_dedup import dedup_indications

router = APIRouter()


@router.post("/run")
async def run_normalizer(normalize: bool = True):
    """Start the durable corpus-wide medical normalizer (Temporal).

    Phase A (grow the action + indication vocabularies from the corpus's distinct
    raw strings) is long and 429-prone, so it runs as ``MedicalNormalizerWorkflow``
    — each LLM batch a separate retriable activity, visible in the Temporal UI and
    surviving worker restarts — rather than a single blocking request that nginx
    would 504. Phase B (relink every use) runs at the tail unless ``normalize`` is
    false. Rejects a second start while one is already running."""
    from temporalio.service import RPCError
    from app.config import settings
    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import MedicalNormalizerWorkflow

    client = await get_temporal_client()
    wf_id = "medical-normalizer"
    try:
        desc = await client.get_workflow_handle(wf_id).describe()
        if desc.status and desc.status.name == "RUNNING":
            raise HTTPException(status_code=409, detail="medical normalizer already running")
    except RPCError:
        pass  # no existing workflow — fine

    handle = await client.start_workflow(
        MedicalNormalizerWorkflow.run,
        args=[80, 100, normalize],
        id=wf_id,
        task_queue=settings.temporal_task_queue,
    )
    return {"status": "started", "workflow_id": handle.id, "run_id": handle.result_run_id}


@router.post("/normalize")
async def normalize(db: AsyncSession = Depends(get_db)):
    """Phase B only — map every PlantMedicinalUse's action_raw + free-text
    indications to the controlled vocabularies (action_id + indication_ids).
    Idempotent corpus-wide recompute, no LLM, so it's quick enough to run inline;
    the medical analog of POST /api/compounds/normalize. Use POST /run to also
    (re)build the vocabularies first."""
    result = await normalize_medical_uses(db)
    return {"status": "completed", **result}


@router.post("/dedup")
async def dedup(apply: bool = False, db: AsyncSession = Depends(get_db)):
    """Merge near-duplicate INDICATION concepts (водянка/асцит/брюшная водянка,
    понос/поносы, отёки/отеки …). Review-first: ``apply=false`` (default) returns
    the proposed merges + the clusters skipped for hierarchy reasons WITHOUT
    writing; ``apply=true`` folds each duplicate's surface forms into the canonical
    concept (recall preserved), repoints every PlantMedicinalUse link + child
    parent_id, deletes the duplicates, commits. Idempotent — a second apply finds
    nothing left to merge."""
    result = await dedup_indications(db, apply=apply)
    return {"status": "applied" if apply else "dry_run", **result}


@router.get("/actions")
async def list_actions(db: AsyncSession = Depends(get_db)):
    """The action vocabulary with how many use-facts each term normalizes."""
    counts = dict((aid, n) for aid, n in (await db.execute(
        select(PlantMedicinalUse.action_id, func.count())
        .where(PlantMedicinalUse.action_id.isnot(None))
        .group_by(PlantMedicinalUse.action_id)
    )).all())
    rows = (await db.execute(select(MedicinalAction).order_by(MedicinalAction.name))).scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "name_modern": a.name_modern,
            "parent_id": str(a.parent_id) if a.parent_id else None,
            "system": a.system,
            "synonyms": a.synonyms or [],
            "linked_facts": counts.get(a.id, 0),
        }
        for a in rows
    ]


@router.get("/indications")
async def list_indications(db: AsyncSession = Depends(get_db)):
    """The indication vocabulary. ``linked_facts`` counts uses whose
    indication_ids array contains the concept (via && over the GIN index)."""
    rows = (await db.execute(select(Indication).order_by(Indication.name))).scalars().all()
    counts: dict[uuid.UUID, int] = {}
    for ind in rows:
        n = (await db.execute(
            select(func.count()).select_from(PlantMedicinalUse)
            .where(PlantMedicinalUse.indication_ids.any(ind.id))
        )).scalar_one()
        counts[ind.id] = n
    return [
        {
            "id": str(i.id),
            "name": i.name,
            "name_modern": i.name_modern,
            "parent_id": str(i.parent_id) if i.parent_id else None,
            "system": i.system,
            "synonyms": i.synonyms or [],
            "archaic": i.archaic or [],
            "definition": i.definition,
            "linked_facts": counts.get(i.id, 0),
        }
        for i in rows
    ]


@router.get("/indications/{indication_id}")
async def get_indication(indication_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """A single indication concept: its archaic bridge names, its place in the
    hierarchy, and the plants whose medicinal uses normalize to it."""
    ind = (await db.execute(
        select(Indication).where(Indication.id == indication_id)
    )).scalar_one_or_none()
    if ind is None:
        raise HTTPException(status_code=404, detail="Indication not found")

    parent = None
    if ind.parent_id:
        p = (await db.execute(
            select(Indication).where(Indication.id == ind.parent_id)
        )).scalar_one_or_none()
        if p is not None:
            parent = {"id": str(p.id), "name": p.name}

    children = (await db.execute(
        select(Indication).where(Indication.parent_id == ind.id).order_by(Indication.name)
    )).scalars().all()

    # Plants treated for this indication (concept + its descendants).
    concept_ids = [ind.id] + [ch.id for ch in children]
    fact_rows = (await db.execute(
        select(PlantMedicinalUse, Plant)
        .join(Plant, PlantMedicinalUse.plant_id == Plant.id)
        .where(or_(*[PlantMedicinalUse.indication_ids.any(cid) for cid in concept_ids]))
        .order_by(Plant.name)
    )).all()
    plants: dict[str, dict] = {}
    for use, plant in fact_rows:
        pid = str(plant.id)
        entry = plants.setdefault(pid, {
            "id": pid, "name": plant.name, "name_latin": plant.name_latin,
            "parts": [], "raw_indications": [],
        })
        if use.part and use.part not in entry["parts"]:
            entry["parts"].append(use.part)
        if use.indications and use.indications not in entry["raw_indications"]:
            entry["raw_indications"].append(use.indications)

    return {
        "id": str(ind.id),
        "name": ind.name,
        "name_modern": ind.name_modern,
        "parent": parent,
        "parent_id": str(ind.parent_id) if ind.parent_id else None,
        "system": ind.system,
        "synonyms": ind.synonyms or [],
        "archaic": ind.archaic or [],
        "definition": ind.definition,
        "children": [{"id": str(ch.id), "name": ch.name} for ch in children],
        "plants": list(plants.values()),
    }
