# -*- coding: utf-8 -*-
"""Pilot generator for the Layer-2 reader-monograph (docs/RFC-reader-monograph).

For each target plant: fetch the deterministic `?view=field` aggregation → distill
(hard cap) → LLM polish → publish-gate → store in `plant_reader_monograph` with
`reviewed=false` (the FIRST batch is manually reviewed per RFC §8 before it serves).
Idempotent: skips a plant whose `generated_from_hash` already matches (re-run only
regenerates changed input). Targets: env PILOT_IDS (comma) OR the richest cards
(most medicinal_uses — the real cap test) plus Хмель (the RFC's worked example).

Run in the backend container (uvicorn must be up — it reads ?view=field over HTTP):
  docker exec -w /app -e PYTHONPATH=/app backend python scripts/gen_reader_monograph.py
"""
import asyncio
import json
import os

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.reader_monograph import PlantReaderMonograph
from app.services import reader_monograph as rm

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
LIMIT = int(os.getenv("PILOT_LIMIT", "6"))
HMEL = "fb743008-7884-4087-a35e-4dd7360e0c09"  # Хмель обыкновенный (RFC worked example)


async def pick_targets() -> list[str]:
    env = os.getenv("PILOT_IDS")
    if env:
        return [x.strip() for x in env.split(",") if x.strip()]
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT p.id::text FROM plants p JOIN plant_medicinal_uses u ON u.plant_id=p.id "
            "WHERE p.name_latin IS NOT NULL "
            "GROUP BY p.id ORDER BY count(*) DESC LIMIT :k"), {"k": LIMIT})).all()
    ids = [r[0] for r in rows]
    if HMEL not in ids:
        ids.insert(0, HMEL)
    return ids


async def generate_one(client, pid: str) -> dict:
    r = await client.get(f"{BACKEND}/api/plants/{pid}?view=field&fresh=1")
    r.raise_for_status()
    fv = r.json()
    distilled = rm.distill(fv)
    h = rm.input_hash(distilled)
    async with async_session() as db:
        existing = await db.get(PlantReaderMonograph, pid)
        if existing and existing.generated_from_hash == h:
            # Input unchanged → don't re-call the LLM, but RE-RUN the publish-gate
            # (cheap, no LLM) so validator fixes take effect on already-stored rows.
            findings = rm.publish_gate(existing.monograph, distilled)
            existing.gate_findings = findings
            await db.commit()
            return {"pid": pid, "name": fv.get("name"), "regated": True,
                    "p0": rm.has_p0(findings), "findings": [f["check"] for f in findings]}

    polished = await rm.polish(distilled)
    mono = rm.assemble(distilled, polished, h, reviewed=False)
    findings = rm.publish_gate(mono, distilled)

    async with async_session() as db:
        stmt = pg_insert(PlantReaderMonograph).values(
            plant_id=pid, monograph=mono, generated_from_hash=h,
            model=rm.MODEL_TAG, reviewed=False, gate_findings=findings,
        ).on_conflict_do_update(index_elements=["plant_id"], set_={
            "monograph": mono, "generated_from_hash": h, "model": rm.MODEL_TAG,
            "reviewed": False, "gate_findings": findings, "updated_at": text("now()")})
        await db.execute(stmt)
        await db.commit()

    return {"pid": pid, "name": fv.get("name"),
            "uses": f"{len(fv.get('uses', []))}→{len(mono.get('uses', []))}",
            "compounds": f"{len(fv.get('compound_groups', []))}→{len(mono.get('compounds', []))}",
            "p0": rm.has_p0(findings), "findings": [f["check"] for f in findings],
            "verdict": (mono.get("verdict") or "")[:140]}


async def main():
    ids = await pick_targets()
    print(f"targets={len(ids)}", flush=True)
    async with httpx.AsyncClient(timeout=120) as client:
        for pid in ids:
            try:
                res = await generate_one(client, pid)
            except Exception as ex:
                res = {"pid": pid, "error": str(ex)[:120]}
            print(json.dumps(res, ensure_ascii=False), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
