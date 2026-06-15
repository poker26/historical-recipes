# -*- coding: utf-8 -*-
"""Repair OCR-garbled name_latin on the ~2071 'cyrillic-in-latin but clean Russian
name' cards. Validated pipeline:
  - iNat by the card's Russian name (primary truth) + LLM (qwen3-235b) propose;
  - GBIF normalizes both; kingdom of the resolved taxon must match the CARD's
    kingdom (растение→Plantae/Chromista, гриб→Fungi) — kills homonym animals;
  - auto when GBIF-confirmed AND (species epithet matches iNat's ru name OR LLM and
    iNat agree on the same accepted taxon).
Actions: auto -> write GBIF canonical + strip only LLM-flagged junk aliases;
review -> stage an `identity.latin_ocr_garbled` finding (INCREMENTAL upsert) in
/quality; else null the garbled latin. All writes commit per-card; audit log at
/tmp/fix_latin_audit.jsonl. Processed in CHUNKS to bound memory (the gather-all
version OOM-killed at ~600). Resumable: fixed/nulled cards drop out of the filter,
review cards with an existing finding are skipped. Env DQ_LIMIT (0 = all).
"""
import asyncio
import json
import os
import re
import uuid

from sqlalchemy import select, text

from app.database import async_session
from app.models.plant import Plant
from app.models.data_quality import DataQualityFinding
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
from app.services.data_quality.framework import norm
from app.services.inaturalist import INAT_BASE, _HEADERS
import httpx

LIMIT = int(os.getenv("DQ_LIMIT", "0"))
CHUNK = 100
CONCURRENCY = 3
CHECK_ID = "identity.latin_ocr_garbled"
AUDIT = "/tmp/fix_latin_audit.jsonl"

FILTER = r"""
    name_latin ~ '[А-Яа-яЁё]'
    AND NOT (
        name ~ '^[0-9]'
        OR name ~ '^[A-ZА-ЯЁ]\.'
        OR name ~ '[@{}\[\]\$<>|]'
        OR name ~ '[A-Za-z]'
    )
"""

SYS_FULL = (
    "Ты ботаник-таксономист. Дана карточка растения из исторического русского источника: "
    "русское название, OCR-искажённая латынь (кириллица вместо латиницы — испорченный бином) "
    "и исторические синонимы (тоже OCR, часть мусор). Задачи:\n"
    "1) Определи ПРИНЯТЫЙ научный бином (род+вид). Опирайся на русское название И на искажённую "
    "латынь; если виден конкретный вид — сохрани его, не подменяй на самый известный вид рода. "
    "Не уверен — \"UNKNOWN\". Не выдумывай.\n"
    "2) drop — список синонимов, которые являются OCR-мусором/географией/обрывками/иностранными "
    "(осмысленные русские названия НЕ трогай).\n"
    "Строго JSON: {\"latin\": \"Genus species\"|\"UNKNOWN\", \"drop\": []}."
)

_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")
_audit_lock = asyncio.Lock()
_gbif_cache: dict = {}
_counter = {"n": 0, "auto": 0, "review": 0, "null": 0}


def ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def strong_match(card, ru):
    cw, iw = ruwords(card), ruwords(ru)
    if not cw or not iw or iw[0] != cw[0]:
        return False
    if len(cw) == 1 and len(iw) == 1:
        return True
    return cw[-1] == iw[-1]


def king_ok(card_kingdom, gbif_kingdom):
    if not gbif_kingdom:
        return False
    if (card_kingdom or "").startswith("гриб"):
        return gbif_kingdom == "Fungi"
    return gbif_kingdom in ("Plantae", "Chromista")


async def gbif(client, sci):
    if not sci:
        return None
    key = norm_bino(sci)
    if key in _gbif_cache:
        return _gbif_cache[key]
    g = await _resolve_one(client, sci)
    _gbif_cache[key] = g
    return g


async def inat_by_ru(client, name):
    qs = [name]
    w = ruwords(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    if w:
        qs.append(w[0])
    for q in qs:
        params = {"q": q, "locale": "ru", "per_page": 5, "is_active": "true"}
        for attempt in range(4):
            try:
                resp = await client.get(f"{INAT_BASE}/taxa", params=params, headers=_HEADERS)
            except Exception:
                return None
            if resp.status_code == 429:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                break
            results = resp.json().get("results", [])
            sp = [r for r in results if r.get("rank") == "species"]
            cand = sp[0] if sp else None
            if cand:
                return {"sci": cand.get("name"), "ru": cand.get("preferred_common_name")}
            break
    return None


async def audit(rec):
    async with _audit_lock:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def stage_finding(pid, name, latin, candidate, evidence, drop):
    """Incremental upsert of one review finding (no full-set stale reconciliation)."""
    async with async_session() as s:
        await s.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:fix AS jsonb), false, 'open', now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              title = EXCLUDED.title, evidence = EXCLUDED.evidence,
              suggested_fix = EXCLUDED.suggested_fix, last_seen = now()
        """), {
            "id": str(uuid.uuid4()), "cid": CHECK_ID, "eid": str(pid),
            "title": f"«{name}»: латынь OCR-битая, кандидат {candidate}",
            "ev": json.dumps(evidence, ensure_ascii=False),
            "fix": json.dumps({"action": "set_latin", "plant_id": str(pid),
                               "candidate": candidate, "aliases_drop": drop}, ensure_ascii=False),
        })
        await s.commit()


async def process(client, sem, row):
    pid, name, latin, hist, kingdom = row
    hist = list(hist or [])
    async with sem:
        try:
            user = json.dumps({"name": name, "garbled_latin": latin, "aliases": hist}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS_FULL}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=1800,
            )
            llm_sci = (llm.get("latin") or "").strip()
            drop = llm.get("drop") or []
        except Exception:
            llm_sci, drop = "", []
        if llm_sci.upper() == "UNKNOWN":
            llm_sci = ""
        inat = await inat_by_ru(client, name)
        inat_sci = (inat or {}).get("sci")
        g_inat = await gbif(client, inat_sci)
        g_llm = await gbif(client, llm_sci)

    inat_ru = (inat or {}).get("ru")
    strong = strong_match(name, inat_ru)
    k_inat, k_llm = (g_inat or {}).get("usage_key"), (g_llm or {}).get("usage_key")
    agree = bool(norm_bino(inat_sci)) and (
        norm_bino(inat_sci) == norm_bino(llm_sci) or (k_inat and k_llm and k_inat == k_llm))
    g = g_inat or {}
    confirmed = (g.get("match_type") or "").upper() in ("EXACT", "FUZZY") and (g.get("confidence") or 0) >= 85

    drop_norm = {norm(d) for d in drop}
    new_hist = [a for a in hist if norm(a) not in drop_norm]

    if inat_sci and confirmed and king_ok(kingdom, g.get("kingdom")) and (strong or agree):
        canonical = g.get("canonical")
        async with async_session() as s:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = canonical
                if len(new_hist) != len(hist):
                    p.names_historical = new_hist
                await s.commit()
        await audit({"id": str(pid), "name": name, "action": "auto", "old_latin": latin,
                     "new_latin": canonical, "dropped_aliases": [a for a in hist if a not in new_hist]})
        _counter["auto"] += 1
    elif inat_sci or llm_sci:
        await stage_finding(pid, name, latin,
                            (g_inat or g_llm or {}).get("canonical") or inat_sci or llm_sci,
                            {"name": name, "garbled_latin": latin, "inat": inat_sci,
                             "inat_ru": inat_ru, "llm": llm_sci, "gbif_kingdom": g.get("kingdom")},
                            drop)
        _counter["review"] += 1
    else:
        async with async_session() as s:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = None
                await s.commit()
        await audit({"id": str(pid), "name": name, "action": "null", "old_latin": latin, "new_latin": None})
        _counter["null"] += 1

    _counter["n"] += 1


async def main():
    async with async_session() as db:
        q = f"SELECT id, name, name_latin, names_historical, kingdom FROM plants WHERE {FILTER} ORDER BY id"
        if LIMIT:
            q += f" LIMIT {LIMIT}"
        rows = (await db.execute(text(q))).all()
        done = {e for (e,) in (await db.execute(
            select(DataQualityFinding.entity_id).where(DataQualityFinding.check_id == CHECK_ID)
        )).all()}
    todo = [r for r in rows if str(r[0]) not in done]
    print(f"candidates={len(rows)} already_staged={len(rows) - len(todo)} todo={len(todo)}", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            res = await asyncio.gather(*[process(client, sem, r) for r in chunk], return_exceptions=True)
            errs = [r for r in res if isinstance(r, Exception)]
            if errs:
                print(f"  ({len(errs)} card errors this chunk, e.g. {str(errs[0])[:70]})", flush=True)
            print(f"... {min(i + CHUNK, len(todo))}/{len(todo)} done "
                  f"(auto={_counter['auto']} review={_counter['review']} null={_counter['null']})", flush=True)

    print(f"DONE n={_counter['n']} auto={_counter['auto']} review={_counter['review']} null={_counter['null']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
