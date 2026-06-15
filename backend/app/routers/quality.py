"""Data-quality / «линтер гербария» API.

Run validator sweeps and triage the findings they produce. Validators are
pure-read; fixing is a separate explicit step. v1 runs the sweep synchronously
(the seed checks are fast SQL); a durable `DataQualitySweepWorkflow` can wrap
this later for scheduling.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.data_quality import DataQualityFinding
from app.services.data_quality.framework import run_sweep, registered_checks
import app.services.data_quality.validators  # noqa: F401  — registers validators

router = APIRouter()


class FindingPatch(BaseModel):
    status: str                       # confirmed | dismissed | fixed | open
    note: str | None = None
    resolved_by: str | None = "admin"


@router.get("/checks")
async def list_checks():
    """The validator catalogue: check_id → severity / auto_fixable / description."""
    return registered_checks()


@router.post("/resolve-taxonomy")
async def resolve_taxonomy(limit: int = Query(500, le=2000)):
    """Resolve up to `limit` not-yet-cached plant binomials against GBIF and cache
    them. Idempotent + resumable — call repeatedly until `resolved` is 0. Must be
    run (at least once over the corpus) before the identity.* checks have data.
    """
    from app.services.data_quality.taxonomy import populate_cache
    return await populate_cache(limit=limit)


@router.post("/adjudicate")
async def adjudicate(check_id: str = Query(...), limit: int = Query(50, le=500)):
    """Run the LLM adjudicator over up to `limit` not-yet-judged open findings of
    `check_id`. Writes a grounded verdict (real/false_positive/uncertain) + action
    onto each finding. Idempotent — skips already-judged findings. Call repeatedly.
    """
    from app.services.data_quality.adjudicator import run_adjudication
    return await run_adjudication(check_id, limit=limit)


@router.post("/bulk-apply")
async def bulk_apply(check_id: str = Query(...), min_confidence: float = Query(0.9),
                     db: AsyncSession = Depends(get_db)):
    """Apply the high-confidence LLM verdicts for a check in one go:
      * llm_verdict=false_positive (conf ≥ min) → status `dismissed`
      * llm_verdict=real + auto_fixable + a known action (conf ≥ min) → run the fix, `fixed`
    Destructive actions stay out of this (only strip_aliases / delete_recipe… that
    the apply executor knows). Returns counts.
    """
    rows = (await db.execute(
        select(DataQualityFinding).where(
            DataQualityFinding.check_id == check_id,
            DataQualityFinding.status == "open",
            DataQualityFinding.llm_confidence >= min_confidence,
        ))).scalars().all()
    dismissed = applied = skipped = 0
    now = datetime.now(timezone.utc)
    for f in rows:
        if f.llm_verdict == "false_positive":
            f.status = "dismissed"; f.resolved_by = "llm-bulk"; f.resolved_at = now
            f.note = (f.note or "") + " [LLM: false positive]"
            dismissed += 1
        elif f.llm_verdict == "real" and f.llm_action in ("strip_aliases", "delete_recipe_and_qdrant"):
            # LLM verdict is the gate here (not the static auto_fixable flag): a
            # high-confidence «real» with a known safe action is applied.
            try:
                await _apply_fix_action(db, f)
                f.status = "fixed"; f.resolved_by = "llm-bulk"; f.resolved_at = now
                applied += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1
    await db.commit()
    return {"check_id": check_id, "dismissed": dismissed, "applied": applied, "skipped": skipped}


@router.post("/sweep")
async def sweep(check_ids: list[str] | None = None):
    """Run validators (all registered, or a given subset) and upsert findings.

    Returns per-check counts (found / new / updated / staled). Read-only on the
    corpus; only writes to `data_quality_findings`.
    """
    return {"results": await run_sweep(check_ids)}


@router.get("/findings")
async def list_findings(
    check_id: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query("open"),
    entity_type: str | None = Query(None),
    llm_verdict: str | None = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List findings with filters. Defaults to open findings."""
    q = select(DataQualityFinding)
    if check_id:
        q = q.where(DataQualityFinding.check_id == check_id)
    if severity:
        q = q.where(DataQualityFinding.severity == severity)
    if status:
        q = q.where(DataQualityFinding.status == status)
    if entity_type:
        q = q.where(DataQualityFinding.entity_type == entity_type)
    if llm_verdict:
        q = q.where(DataQualityFinding.llm_verdict == llm_verdict)
    q = q.order_by(DataQualityFinding.severity, DataQualityFinding.last_seen.desc())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return [
        {
            "id": str(f.id), "check_id": f.check_id, "severity": f.severity,
            "entity_type": f.entity_type, "entity_id": f.entity_id,
            "title": f.title, "evidence": f.evidence, "suggested_fix": f.suggested_fix,
            "auto_fixable": f.auto_fixable, "status": f.status,
            "first_seen": f.first_seen, "last_seen": f.last_seen, "note": f.note,
            "llm_verdict": f.llm_verdict, "llm_confidence": f.llm_confidence,
            "llm_action": f.llm_action, "llm_reasoning": f.llm_reasoning,
        }
        for f in rows
    ]


_TRIAGE_STATUSES = {"open", "confirmed", "dismissed", "fixed"}


@router.patch("/findings/{finding_id}")
async def triage_finding(finding_id: str, patch: FindingPatch,
                         db: AsyncSession = Depends(get_db)):
    """Set a finding's triage status (confirmed / dismissed / fixed / open).

    confirmed/dismissed are STICKY — a later sweep won't resurrect them to open.
    """
    if patch.status not in _TRIAGE_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_TRIAGE_STATUSES)}")
    f = (await db.execute(
        select(DataQualityFinding).where(DataQualityFinding.id == finding_id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "finding not found")
    f.status = patch.status
    f.note = patch.note
    f.resolved_by = patch.resolved_by
    f.resolved_at = datetime.now(timezone.utc) if patch.status != "open" else None
    await db.commit()
    return {"id": str(f.id), "status": f.status}


async def _apply_fix_action(db: AsyncSession, f: DataQualityFinding) -> dict:
    """Execute a finding's suggested_fix action (mutates data). Shared by the
    single-finding apply and the LLM bulk-apply. Raises ValueError on unknown
    action. Does NOT commit or change status — the caller does."""
    fix = f.suggested_fix or {}
    action = fix.get("action")
    if action == "delete_recipe_and_qdrant":
        from app.models.recipe import Recipe
        from app.services import qdrant as qdrant_svc
        rid = fix["recipe_id"]
        rec = (await db.execute(select(Recipe).where(Recipe.id == rid))).scalar_one_or_none()
        if rec:
            if rec.qdrant_point_id and rec.qdrant_collection:
                try:
                    await qdrant_svc.delete_points(rec.qdrant_collection, [rec.qdrant_point_id])
                except Exception:
                    pass
            await db.delete(rec)
        return {"deleted_recipe": rid}
    if action == "strip_aliases":
        from app.models.plant import Plant
        pid = fix["plant_id"]
        to_strip = set(fix.get("aliases", []))
        pl = (await db.execute(select(Plant).where(Plant.id == pid))).scalar_one_or_none()
        if pl and pl.names_historical:
            pl.names_historical = [h for h in pl.names_historical if h not in to_strip]
        return {"plant_id": pid, "stripped": sorted(to_strip)}
    raise ValueError(f"no executor for action {action!r}")


@router.post("/findings/{finding_id}/apply")
async def apply_finding_fix(finding_id: str, db: AsyncSession = Depends(get_db)):
    """Execute a finding's suggested auto-fix, then mark it `fixed`. Destructive —
    each action is explicit. Supported: delete_recipe_and_qdrant, strip_aliases."""
    f = (await db.execute(
        select(DataQualityFinding).where(DataQualityFinding.id == finding_id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "finding not found")
    if not f.auto_fixable:
        raise HTTPException(400, "finding is not auto-fixable — triage by hand")
    try:
        result = await _apply_fix_action(db, f)
    except ValueError as e:
        raise HTTPException(400, str(e))
    f.status = "fixed"
    f.resolved_by = "admin"
    f.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(f.id), "status": "fixed", "applied": (f.suggested_fix or {}).get("action"), **result}


@router.post("/findings/{finding_id}/delete-entity")
async def delete_entity(finding_id: str, db: AsyncSession = Depends(get_db)):
    """Delete the plant/recipe a finding is about — for junk that shouldn't exist
    at all (e.g. «кашалот» = an animal miscarded as a plant). Removes the row
    (child facts cascade), its qdrant point, marks this finding `fixed`, and ages
    any OTHER open findings about the same entity to `stale` (the entity is gone).
    """
    from app.services import qdrant as qdrant_svc

    f = (await db.execute(
        select(DataQualityFinding).where(DataQualityFinding.id == finding_id)
    )).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "finding not found")

    if f.entity_type == "plant":
        from app.models.plant import Plant
        try:
            await qdrant_svc.delete_points("plants_v2", [f.entity_id])  # point id == plant id
        except Exception:
            pass
        await db.execute(delete(Plant).where(Plant.id == f.entity_id))
        deleted = {"plant": f.entity_id}
    elif f.entity_type == "recipe":
        from app.models.recipe import Recipe
        rec = (await db.execute(select(Recipe).where(Recipe.id == f.entity_id))).scalar_one_or_none()
        if rec and rec.qdrant_point_id and rec.qdrant_collection:
            try:
                await qdrant_svc.delete_points(rec.qdrant_collection, [rec.qdrant_point_id])
            except Exception:
                pass
        await db.execute(delete(Recipe).where(Recipe.id == f.entity_id))
        deleted = {"recipe": f.entity_id}
    else:
        raise HTTPException(400, f"can't delete entity_type {f.entity_type!r} (only plant/recipe)")

    f.status = "fixed"
    f.resolved_by = "admin-delete"
    f.resolved_at = datetime.now(timezone.utc)
    await db.execute(
        update(DataQualityFinding)
        .where(DataQualityFinding.entity_id == f.entity_id,
               DataQualityFinding.id != f.id,
               DataQualityFinding.status == "open")
        .values(status="stale", last_seen=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"deleted": deleted, "finding": str(f.id)}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Counts grouped by (check_id, severity, status) — the dashboard at a glance."""
    rows = (await db.execute(
        select(DataQualityFinding.check_id, DataQualityFinding.severity,
               DataQualityFinding.status, func.count())
        .group_by(DataQualityFinding.check_id, DataQualityFinding.severity,
                  DataQualityFinding.status)
        .order_by(DataQualityFinding.severity, DataQualityFinding.check_id)
    )).all()
    return [
        {"check_id": c, "severity": s, "status": st, "count": n}
        for c, s, st, n in rows
    ]
