# -*- coding: utf-8 -*-
"""Promote cards whose `name` is a clean Latin taxon but name_latin is NULL
(Broussonetia, Bryonia alba, Erysimum, …). GBIF resolves the Latin name directly
(no LLM/iNat needed). Plantae/Fungi/Chromista → write name_latin = GBIF canonical
(the running iNat enrichment then fills name_modern + photo, and a later rename
pass cleans `name`). Non-plant taxa GBIF resolves to Animalia/etc (Camelus=camel,
Cervus=deer, Cinnabaris=mineral) → flag delete_or_rekingdom (NOT a plant). Pharma
genitives (Cortex/Folia/Rosae…) are excluded by the filter. Audit + idempotent.
"""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.database import async_session
from app.models.plant import Plant
from app.services.data_quality.taxonomy import _resolve_one
import httpx

CHECK_ID = "identity.name_ocr_garbled"
AUDIT = "/tmp/promote_latin_audit.jsonl"

PHARMA = ("name ~ '^(Cortex|Folia|Folium|Semina|Semen|Herba|Radix|Radices|Flores|Flos|"
          "Fructus|Oleum|Rhizoma|Gummi|Resina|Cormus|Stigmata|Bulbus|Tuber|Lignum|Gemmae|"
          "Strobili|Pericarpium|Aetheroleum|Rosae|Pini) '")
FILTER = f"name_latin IS NULL AND name ~ '^[A-Z][a-z]' AND name !~ '[А-Яа-яЁё0-9]' AND NOT ({PHARMA})"


async def upsert_finding(s, pid, name, title, action, ev):
    await s.execute(text("""
        INSERT INTO data_quality_findings
          (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
           auto_fixable, status, first_seen, last_seen)
        VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                CAST(:fix AS jsonb), false, 'open', now(), now())
        ON CONFLICT (check_id, entity_id) DO UPDATE SET
          title=EXCLUDED.title, evidence=EXCLUDED.evidence,
          suggested_fix=EXCLUDED.suggested_fix, last_seen=now()
    """), {"id": str(uuid.uuid4()), "cid": CHECK_ID, "eid": str(pid), "title": title,
           "ev": json.dumps(ev, ensure_ascii=False),
           "fix": json.dumps({"action": action, "plant_id": str(pid)}, ensure_ascii=False)})


async def process(client, sem, pid, name):
    async with sem:
        g = await _resolve_one(client, name)
    king = (g or {}).get("kingdom")
    mt = ((g or {}).get("match_type") or "").upper()
    conf = (g or {}).get("confidence") or 0
    canonical = (g or {}).get("canonical")
    async with async_session() as s:
        if g and mt in ("EXACT", "FUZZY") and conf >= 85 and king in ("Plantae", "Fungi", "Chromista") and canonical:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = canonical
                await s.commit()
            act = "promote"
        elif g and king and king not in ("Plantae", "Fungi", "Chromista"):
            await upsert_finding(s, pid, name, f"«{name}»: не растение — GBIF kingdom={king} (удалить/сменить kingdom)",
                                 "delete_or_rekingdom", {"name": name, "gbif_kingdom": king, "canonical": canonical})
            await s.commit()
            act = f"nonplant:{king}"
        else:
            await upsert_finding(s, pid, name, f"«{name}»: латынь не резолвится в GBIF — проверить вручную",
                                 "set_latin", {"name": name, "gbif": g})
            await s.commit()
            act = "review"
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": str(pid), "name": name, "action": act, "canonical": canonical}, ensure_ascii=False) + "\n")
    return act


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(f"SELECT id, name FROM plants WHERE {FILTER} ORDER BY name"))).all()
    print(f"candidates={len(rows)}", flush=True)
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=20) as client:
        acts = await asyncio.gather(*[process(client, sem, r[0], r[1]) for r in rows], return_exceptions=True)
    tally = {}
    for a in acts:
        k = "ERR" if isinstance(a, Exception) else (a.split(":")[0] if isinstance(a, str) and a.startswith("nonplant") else a)
        tally[k] = tally.get(k, 0) + 1
    print("TALLY:", tally, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
