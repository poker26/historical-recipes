# -*- coding: utf-8 -*-
"""Phase 1e — drain the `identity.latin_ocr_garbled` REVIEW queue with a second,
independent adjudication of each staged candidate (the residue the auto-gate in
fix_latin_run.py couldn't confirm). Two failure modes dominate the queue:
  * the candidate is an ANIMAL homonym (iNat-by-russian-name mis-hit, e.g.
    «персидский ревень»→Natrix natrix) — GBIF kingdom of the CANDIDATE kills it;
  * the candidate is actually CORRECT but missed strong/agree (e.g. «первоцвет
    крупночашечный»→Primula macrocalyx) — a strong LLM (qwen3-235b) + GBIF EXACT
    confirms it.
Decision per finding (fills the row's llm_* adjudication layer + audit-logged):
  CONFIRM  → GBIF(candidate) is Plantae/Fungi/Chromista, EXACT/FUZZY conf≥90, AND
             qwen3-235b verdict=YES → apply candidate (set name_latin, drop junk
             aliases, inat_synced_at=NULL); finding → status='fixed'.
  REJECT   → GBIF(candidate) kingdom not plant/fungi (animal homonym). If the LLM
             offers a better_latin that GBIF-confirms as a plant (EXACT) → apply
             that instead; else NULL the garbled latin. finding → status='fixed'.
  KEEP     → kingdom ok but LLM NO/UNSURE (genus/multi-species/ambiguous) → leave
             status='open', but record the verdict so a human sees the reasoning.
Idempotent: skips findings already adjudicated (llm_at IS NOT NULL). CHUNK×100.
Audit → /tmp/fix_latin_adj_audit.jsonl. Env DQ_LIMIT (0 = all). Operates ONLY on
latin-NOT-NULL cyrillic-garbage cards → disjoint from the running latin-backfill.
"""
import asyncio
import json
import os
import re
import uuid

from sqlalchemy import text

from app.database import async_session
from app.models.plant import Plant
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
from app.services.data_quality.framework import norm
import httpx

LIMIT = int(os.getenv("DQ_LIMIT", "0"))
CHUNK = 100
CONCURRENCY = 3
CHECK_ID = "identity.latin_ocr_garbled"
MODEL_TAG = "qwen3-235b"
AUDIT = "/tmp/fix_latin_adj_audit.jsonl"

SYS = (
    "Ты ботаник-таксономист. Дана карточка растения/гриба из исторического русского "
    "источника: русское название, OCR-искажённая латынь (кириллица вместо латиницы) и "
    "ПРЕДЛАГАЕМЫЙ принятый бином (кандидат из GBIF/iNat). Реши строго:\n"
    "Является ли кандидат действительно правильным ПРИНЯТЫМ научным биномом ИМЕННО ЭТОГО "
    "растения? Учитывай русское название и осмысленную часть искажённой латыни.\n"
    "verdict=NO если: кандидат — животное/другой таксон; ИЛИ это родовая/сборная карточка "
    "(несколько разных видов) — для неё единый вид навязывать нельзя.\n"
    "verdict=UNSURE если не можешь уверенно подтвердить.\n"
    "Если знаешь ЛУЧШИЙ правильный бином — верни его в better_latin (иначе null).\n"
    "Строго JSON: {\"verdict\": \"YES\"|\"NO\"|\"UNSURE\", \"better_latin\": \"Genus species\"|null, "
    "\"reason\": \"кратко\"}."
)

_LAT = re.compile(r"[A-Za-z]+")
_audit_lock = asyncio.Lock()
_gbif_cache: dict = {}
_counter = {"n": 0, "confirm": 0, "reject_null": 0, "reject_better": 0, "keep": 0}


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def king_ok(card_kingdom, gbif_kingdom):
    if not gbif_kingdom:
        return False
    if (card_kingdom or "").startswith("гриб"):
        return gbif_kingdom == "Fungi"
    return gbif_kingdom in ("Plantae", "Chromista")


def confirmed_plant(g, card_kingdom, exact_only=False):
    if not g:
        return False
    mt = (g.get("match_type") or "").upper()
    ok_mt = mt == "EXACT" if exact_only else mt in ("EXACT", "FUZZY")
    return ok_mt and (g.get("confidence") or 0) >= 90 and king_ok(card_kingdom, g.get("kingdom"))


async def gbif(client, sci):
    if not sci:
        return None
    key = norm_bino(sci)
    if not key:
        return None
    if key in _gbif_cache:
        return _gbif_cache[key]
    g = await _resolve_one(client, sci)
    _gbif_cache[key] = g
    return g


async def audit(rec):
    async with _audit_lock:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def adjudicate_finding(fid, verdict, conf, action, reason):
    applied = action in ("set_latin", "null_latin")
    async with async_session() as s:
        await s.execute(text("""
            UPDATE data_quality_findings SET
              llm_verdict=:v, llm_confidence=:c, llm_action=:a, llm_reasoning=:r,
              llm_model=:m, llm_at=now(),
              status = CASE WHEN :applied THEN 'fixed' ELSE status END,
              resolved_by = CASE WHEN :applied THEN 'adjudicator' ELSE resolved_by END,
              resolved_at = CASE WHEN :applied THEN now() ELSE resolved_at END,
              last_seen = now()
            WHERE id = CAST(:id AS uuid)
        """), {"v": verdict, "c": conf, "a": action, "r": reason[:480],
               "m": MODEL_TAG, "id": str(fid), "applied": applied})
        await s.commit()


async def apply_latin(pid, canonical, drop):
    drop_norm = {norm(d) for d in (drop or [])}
    async with async_session() as s:
        p = await s.get(Plant, uuid.UUID(str(pid)))
        if not p:
            return None
        old = p.name_latin
        p.name_latin = canonical
        if drop_norm:
            hist = list(p.names_historical or [])
            p.names_historical = [a for a in hist if norm(a) not in drop_norm]
        p.inat_synced_at = None
        await s.commit()
        return old


async def null_latin(pid):
    async with async_session() as s:
        p = await s.get(Plant, uuid.UUID(str(pid)))
        if not p:
            return None
        old = p.name_latin
        p.name_latin = None
        await s.commit()
        return old


async def process(client, sem, row):
    fid, pid, name, latin, kingdom, fix = row
    candidate = (fix or {}).get("candidate")
    drop = (fix or {}).get("aliases_drop") or []
    g_cand = await gbif(client, candidate)
    cand_kingdom = (g_cand or {}).get("kingdom")
    cand_is_plant = king_ok(kingdom, cand_kingdom)

    async with sem:
        try:
            user = json.dumps({"name": name, "garbled_latin": latin, "candidate": candidate,
                               "gbif_canonical": (g_cand or {}).get("canonical"),
                               "gbif_kingdom": cand_kingdom}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=600)
            verdict = (llm.get("verdict") or "UNSURE").upper()
            better = (llm.get("better_latin") or "").strip() or None
            reason = llm.get("reason") or ""
        except Exception as ex:
            verdict, better, reason = "UNSURE", None, f"llm_error:{str(ex)[:60]}"

    # CONFIRM: candidate is a plant, GBIF-EXACT/FUZZY-confirmed, LLM agrees.
    if cand_is_plant and confirmed_plant(g_cand, kingdom) and verdict == "YES":
        canonical = (g_cand or {}).get("canonical") or candidate
        old = await apply_latin(pid, canonical, drop)
        await adjudicate_finding(fid, "real", 0.9, "set_latin", f"confirm {canonical}: {reason}")
        await audit({"id": str(pid), "fid": str(fid), "name": name, "action": "confirm",
                     "old_latin": old, "new_latin": canonical, "reason": reason})
        _counter["confirm"] += 1

    # REJECT candidate (animal homonym / wrong): try the LLM's better_latin, else null.
    elif not cand_is_plant:
        g_better = await gbif(client, better) if better else None
        if better and confirmed_plant(g_better, kingdom, exact_only=True) and verdict in ("YES", "NO"):
            canonical = (g_better or {}).get("canonical") or better
            old = await apply_latin(pid, canonical, drop)
            await adjudicate_finding(fid, "real", 0.8, "set_latin",
                                     f"candidate {cand_kingdom} rejected→better {canonical}: {reason}")
            await audit({"id": str(pid), "fid": str(fid), "name": name, "action": "reject_better",
                         "old_latin": old, "rejected": candidate, "new_latin": canonical, "reason": reason})
            _counter["reject_better"] += 1
        else:
            old = await null_latin(pid)
            await adjudicate_finding(fid, "real", 0.85, "null_latin",
                                     f"candidate is {cand_kingdom} (not plant); no plant resolved: {reason}")
            await audit({"id": str(pid), "fid": str(fid), "name": name, "action": "reject_null",
                         "old_latin": old, "rejected": candidate, "cand_kingdom": cand_kingdom, "reason": reason})
            _counter["reject_null"] += 1

    # KEEP: kingdom ok but LLM not confident (genus/multi-species/ambiguous) — human.
    else:
        vmap = {"NO": "false_positive", "UNSURE": "uncertain", "YES": "uncertain"}
        await adjudicate_finding(fid, vmap.get(verdict, "uncertain"), 0.4, "keep",
                                 f"verdict={verdict} cand_kingdom={cand_kingdom}: {reason}")
        _counter["keep"] += 1

    _counter["n"] += 1


async def main():
    async with async_session() as db:
        q = f"""SELECT f.id, f.entity_id, p.name, p.name_latin, p.kingdom, f.suggested_fix
                FROM data_quality_findings f JOIN plants p ON p.id = f.entity_id::uuid
                WHERE f.check_id = '{CHECK_ID}' AND f.status = 'open' AND f.llm_at IS NULL
                ORDER BY f.entity_id"""
        if LIMIT:
            q += f" LIMIT {LIMIT}"
        rows = (await db.execute(text(q))).all()
    print(f"todo={len(rows)}", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            res = await asyncio.gather(*[process(client, sem, r) for r in chunk], return_exceptions=True)
            errs = [r for r in res if isinstance(r, Exception)]
            if errs:
                print(f"  ({len(errs)} errors, e.g. {str(errs[0])[:80]})", flush=True)
            print(f"... {min(i + CHUNK, len(rows))}/{len(rows)} "
                  f"(confirm={_counter['confirm']} reject_null={_counter['reject_null']} "
                  f"reject_better={_counter['reject_better']} keep={_counter['keep']})", flush=True)

    print(f"DONE {_counter}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
