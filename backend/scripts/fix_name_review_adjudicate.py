# -*- coding: utf-8 -*-
"""Phase 1e (name queue) — adjudicate the `identity.name_ocr_garbled` review residue
(547). Unlike the latin queue these findings carry NO pre-computed candidate and the
NAME itself is broken; latin is mostly NULL. So this is RESOLUTION + ROUTING, not
candidate-judging. Per-finding buckets (fills the row's llm_* layer):
  ROUTE_MERGE   → name is an abbreviated head-noun fragment («П. янгамбийский»,
                  «Ж.-к. лекарственный») — that is the Phase 1f part-fragment class
                  (merge into the parent genus), NOT resolvable standalone here.
                  Tag verdict=uncertain/action=route_merge; leave status='open'.
  ROUTE_NONPLANT→ pharma-latin recipe fragment («Folia Sennae», «Cortex Quercus»)
                  or LLM says not a species. Tag action=route_nonplant; leave open.
  RESOLVE       → a full garbled / old-orthography name («ДУБЪ ЧЕРНИЛЬНО-ОРѢШКОВЫЙ»,
                  «Азгасаиз опофгус») → qwen3-235b normalizes to a modern Russian
                  name + accepted binomial; GBIF EXACT/FUZZY≥90 + kingdom gate
                  confirms → apply name_latin (+ name_modern); status='fixed'. Else
                  keep open with the verdict recorded.
Idempotent (skips llm_at IS NOT NULL). CHUNK×100. Audit /tmp/fix_name_adj_audit.jsonl.
Env DQ_LIMIT (0 = all). Touches only its own findings → disjoint from latin-backfill.
"""
import asyncio
import json
import os
import re
import uuid

from sqlalchemy import text

from app.database import async_session
from app.models.plant import Plant
from app.services.data_quality.validators.name_junk import clean_display_name
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
import httpx

LIMIT = int(os.getenv("DQ_LIMIT", "0"))
CHUNK = 100
CONCURRENCY = 3
CHECK_ID = "identity.name_ocr_garbled"
MODEL_TAG = "qwen3-235b"
AUDIT = "/tmp/fix_name_adj_audit.jsonl"

ABBREV = re.compile(r"^[А-ЯЁA-Z]\.")
PHARMA = re.compile(r"^(folia|cortex|radix|herba|flores|fructus|semen|rhizoma|oleum|gemmae|stigmata)\b", re.I)
_LAT = re.compile(r"[A-Za-z]+")

SYS = (
    "Ты ботаник-таксономист и палеограф. Дано искажённое (OCR/старая орфография) название "
    "растения или гриба из исторического русского источника, иногда с обрывком латыни. Задачи:\n"
    "1) modern_name — современное русское написание (нормализуй ъ/ѣ/і, OCR-ошибки). Если не "
    "узнаёшь — пустая строка.\n"
    "2) latin — ПРИНЯТЫЙ научный бином (род+вид) ИЛИ род, если вид не определить. Не уверен — "
    "\"UNKNOWN\". Не выдумывай известный вид рода.\n"
    "3) is_plant — false, если это рецептурный фрагмент (Folia/Cortex…), химикат, орган растения "
    "или не-таксон.\n"
    "Строго JSON: {\"modern_name\": str, \"latin\": \"Genus species\"|\"Genus\"|\"UNKNOWN\", "
    "\"is_plant\": bool, \"reason\": \"кратко\"}."
)

_audit_lock = asyncio.Lock()
_gbif_cache: dict = {}
_counter = {"n": 0, "resolve": 0, "route_merge": 0, "route_nonplant": 0, "keep": 0}


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else (t[0].lower() if t else "")


def king_ok(card_kingdom, gbif_kingdom):
    if not gbif_kingdom:
        return False
    if (card_kingdom or "").startswith("гриб"):
        return gbif_kingdom == "Fungi"
    return gbif_kingdom in ("Plantae", "Chromista")


def confirmed_plant(g, card_kingdom):
    if not g:
        return False
    mt = (g.get("match_type") or "").upper()
    return mt in ("EXACT", "FUZZY") and (g.get("confidence") or 0) >= 90 and king_ok(card_kingdom, g.get("kingdom"))


async def gbif(client, sci):
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


async def adjudicate(fid, verdict, conf, action, reason):
    applied = action == "resolve"
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


async def process(client, sem, row):
    fid, pid, name, latin, kingdom = row
    name = name or ""

    # Bucket 1: abbreviated head-noun fragment → Phase 1f merge (route, don't resolve).
    if ABBREV.match(name.strip()):
        await adjudicate(fid, "uncertain", 0.5, "route_merge",
                         "abbreviated head-noun fragment → 1f merge into parent genus")
        _counter["route_merge"] += 1
        _counter["n"] += 1
        return

    # Bucket 2: pharma-latin recipe fragment → non-plant.
    if PHARMA.match(name.strip()):
        await adjudicate(fid, "real", 0.7, "route_nonplant",
                         "pharma-latin recipe fragment (organ/preparation, not a species)")
        _counter["route_nonplant"] += 1
        _counter["n"] += 1
        return

    async with sem:
        try:
            user = json.dumps({"name": name, "latin_fragment": latin}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=700)
            modern = (llm.get("modern_name") or "").strip()
            cand = (llm.get("latin") or "").strip()
            is_plant = llm.get("is_plant", True)
            reason = llm.get("reason") or ""
        except Exception as ex:
            modern, cand, is_plant, reason = "", "UNKNOWN", True, f"llm_error:{str(ex)[:60]}"

    if is_plant is False:
        # A Cyrillic-only name the LLM punted on is more likely a folk/garbled PLANT
        # name (Устели-поле, Петушки=Iris) than a true non-plant → keep for human,
        # don't mislabel. Reserve route_nonplant for clear non-plant signals
        # (Latin mineral/chemical/pharma names: Chrysocolla, Cuprum, Corallium…).
        cyr_only = bool(re.search(r"[А-Яа-яЁё]", name)) and not _LAT.search(name)
        if cyr_only:
            await adjudicate(fid, "uncertain", 0.4, "keep",
                             f"LLM не распознал; кириллич. имя — возможно народное/битое: {reason}")
            _counter["keep"] += 1
        else:
            await adjudicate(fid, "real", 0.6, "route_nonplant", f"LLM: not a species — {reason}")
            _counter["route_nonplant"] += 1
        _counter["n"] += 1
        return

    g = await gbif(client, cand) if cand and cand.upper() != "UNKNOWN" else None
    # Require a binomial (genus+species); a bare genus stays review (too coarse to apply).
    is_binomial = len(_LAT.findall(cand or "")) >= 2
    if g and is_binomial and confirmed_plant(g, kingdom):
        canonical = g.get("canonical") or cand
        async with async_session() as s:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = canonical
                # Чистим то, что пишем: LLM нормализует OCR-строку целиком, а в ней
                # к имени бывает приклеена ссылка на источник — так в проде появилась
                # «Липа коринфскаяhttps://www.google.ru/books/…» (поймано в «Эфире»).
                modern = clean_display_name(modern)
                if modern and not (p.name_modern or "").strip():
                    p.name_modern = modern
                p.inat_synced_at = None
                await s.commit()
        await adjudicate(fid, "real", 0.85, "resolve", f"resolved {canonical}: {reason}")
        await audit({"id": str(pid), "fid": str(fid), "name": name, "action": "resolve",
                     "modern": modern, "new_latin": canonical, "reason": reason})
        _counter["resolve"] += 1
    else:
        await adjudicate(fid, "uncertain", 0.4, "keep",
                         f"unresolved: cand={cand} modern={modern} — {reason}")
        _counter["keep"] += 1

    _counter["n"] += 1


async def main():
    async with async_session() as db:
        q = f"""SELECT f.id, f.entity_id, p.name, p.name_latin, p.kingdom
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
                  f"(resolve={_counter['resolve']} route_merge={_counter['route_merge']} "
                  f"route_nonplant={_counter['route_nonplant']} keep={_counter['keep']})", flush=True)

    print(f"DONE {_counter}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
