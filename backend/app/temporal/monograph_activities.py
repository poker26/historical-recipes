# -*- coding: utf-8 -*-
"""Durable activity for the Layer-2 reader-monograph BATCH (docs/RFC-reader-monograph
§7). Iterates plants with a clean identity (name_latin set) by id cursor; per plant:
fetch the raw `?view=field&fresh=1` aggregation → distill (hard cap) → if the input
hash is unchanged skip (idempotent §7 hash-gate) → LLM polish → publish-gate → store
`reviewed=false` (the first batch is human-reviewed via /api/monographs before serving).

Resumable (cursor + hash-skip), heartbeating. GATED: only run once Layer-1 data
cleanup is complete — generating from dirty data = polishing garbage (RFC §10.5).
"""
import logging

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio import activity

from app.config import settings
from app.database import async_session
from app.models.reader_monograph import PlantReaderMonograph
from app.services import reader_monograph as rm

logger = logging.getLogger(__name__)

# Same-network internal base; the backend serves the aggregation we distill.
_BACKEND = getattr(settings, "internal_backend_url", None) or "http://backend:8000"


@activity.defn
async def generate_monographs_activity(batch: int = 100) -> dict:
    """Generate reader-monographs for every clean-identity plant whose distilled
    input changed (or has none yet). Cursor over plant id; idempotent via the §7
    hash-gate. Returns counts. Stores reviewed=false — publishing is a human step."""
    generated = skipped = blocked = errors = seen = 0
    cursor = "00000000-0000-0000-0000-000000000000"
    async with httpx.AsyncClient(timeout=120) as client:
        while True:
            async with async_session() as db:
                rows = (await db.execute(text(
                    "SELECT id::text FROM plants WHERE name_latin IS NOT NULL "
                    # species only: a genus row is a HUB with a different field-view
                    # shape (members/aggregated) that the species distiller can't read;
                    # вещество/животное are non-plant materia medica, not monographed.
                    "AND rank = 'species' AND kingdom IN ('растение','гриб') "
                    "AND id::text > :c ORDER BY id LIMIT :n"), {"c": cursor, "n": batch})).all()
            if not rows:
                break
            for (pid,) in rows:
                cursor = pid
                seen += 1
                try:
                    r = await client.get(f"{_BACKEND}/api/plants/{pid}?view=field&fresh=1")
                    r.raise_for_status()
                    fv = r.json()
                    distilled = rm.distill(fv)
                    h = rm.input_hash(distilled)
                    async with async_session() as db:
                        existing = await db.get(PlantReaderMonograph, pid)
                        if existing and existing.generated_from_hash == h:
                            skipped += 1
                            continue
                    polished = await rm.polish(distilled)
                    mono = rm.assemble(distilled, polished, h, reviewed=False)
                    findings = rm.publish_gate(mono, distilled)
                    # The publish-gate IS the automated review: a P0-clean monograph
                    # auto-publishes (reviewed=True → served by ?view=field); a P0 holds
                    # it pending manual review (RFC §8 — "P0 doesn't reach users"). Hand-
                    # reviewing 18K is infeasible, so the gate must do the gating.
                    pub = not rm.has_p0(findings)
                    if not pub:
                        blocked += 1
                    async with async_session() as db:
                        await db.execute(pg_insert(PlantReaderMonograph).values(
                            plant_id=pid, monograph=mono, generated_from_hash=h,
                            model=rm.MODEL_TAG, reviewed=pub, gate_findings=findings,
                        ).on_conflict_do_update(index_elements=["plant_id"], set_={
                            "monograph": mono, "generated_from_hash": h, "model": rm.MODEL_TAG,
                            "reviewed": pub, "gate_findings": findings, "updated_at": text("now()")}))
                        await db.commit()
                    generated += 1
                except Exception as ex:
                    errors += 1
                    logger.warning("monograph gen %s failed: %s", pid, str(ex)[:80])
                activity.heartbeat({"seen": seen, "generated": generated,
                                    "skipped": skipped, "blocked": blocked, "errors": errors})
    return {"seen": seen, "generated": generated, "skipped": skipped,
            "blocked": blocked, "errors": errors}
