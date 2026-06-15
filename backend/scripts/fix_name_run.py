# -*- coding: utf-8 -*-
"""SECOND PASS — recover identity for the 'broken name + NULL latin' class
(Scopolia, Styrax benzoin, Daucus carota, «Егэг 1 — тростник обыкновенный», …).
These are NOT garbage to delete: they are OCR-mangled / mis-filed real plants, each
often carrying real facts. We resolve identity from the broken NAME (the latin
field is already null) and write it to name_latin + name_modern (both currently
NULL → no collision risk); the broken `name` itself is left untouched.

Per card:
  - LLM (qwen3-235b): is this a plant? accepted binomial? clean Russian name? The
    name may be clean Latin (promote), OCR-garbled Latin, «junk — РусскоеИмя», or a
    non-plant (mineral/animal/product → not a plant).
  - iNat by the (LLM/​tail) Russian name + GBIF normalize; kingdom must match card.
  - auto: GBIF-confirmed plant + kingdom ok + (iNat strong ru-match OR iNat/LLM
    agree) -> name_latin = canonical, name_modern = ru name (if any).
  - non-plant / unresolvable -> stage an `identity.name_ocr_garbled` finding for
    /quality (delete-or-rekingdom decision stays human).
Chunked + incremental + resumable (filled latin drops out; staged cards skipped).
Env DQ_LIMIT (0 = all).
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
from app.services.inaturalist import INAT_BASE, _HEADERS
import httpx

LIMIT = int(os.getenv("DQ_LIMIT", "0"))
CHUNK = 100
CONCURRENCY = 3
CHECK_ID = "identity.name_ocr_garbled"
AUDIT = "/tmp/fix_name_audit.jsonl"

# null latin + a name that is NOT a clean Russian name: has a digit, a Latin letter,
# a junk char, a «—» tail, or a single-letter abbreviation. Clean Russian folk-named
# null-latin cards (legit, untouched) don't match.
FILTER = r"""
    name_latin IS NULL
    AND (
        name ~ '[0-9]'
        OR name ~ '[A-Za-z]'
        OR name ~ '[@{}\[\]\$<>|]'
        OR name ~ '[—–-]'
        OR name ~ '^[А-ЯЁ]\.'
    )
"""

SYS = (
    "Ты ботаник-таксономист. Дан JSON карточки из исторического русского определителя с "
    "ИСПОРЧЕННЫМ OCR (поле латыни пустое): name, family, aliases. Имя может быть: чистой "
    "латынью («Daucus carota»), OCR-искажённой латынью («5{угах Беп2о1п» = Styrax benzoin), "
    "формата «мусор — РусскоеИмя» («Егэг 1 — тростник обыкновенный»), либо ВОВСЕ НЕ растением "
    "(минерал «селитра», животное «самец куропатки», продукт «вино»).\n"
    "ВАЖНО — структура определителя: РОД часто спрятан в поле family как «ЛАТ_РОД Г. — РУС_РОД» "
    "(напр. «ТАТНУВО$ Г. — ЧИНА» = род Lathyrus / Чина), а ВИД — в aliases как «Ч. волосистая» "
    "(сокращённый род + эпитет). Собери русский бином из них: «ЧИНА»+«Ч. волосистая» = «Чина "
    "волосистая» = Lathyrus pilosus. Используй name+family+aliases вместе.\n"
    "Определи:\n"
    "1) is_plant: true/false (false для минералов/животных/продуктов/частей-сырья);\n"
    "2) latin: принятый научный бином (род+вид) если растение и уверен, иначе \"UNKNOWN\";\n"
    "3) russian: чистое современное русское название растения, иначе null.\n"
    "Не выдумывай. Строго JSON: {\"is_plant\": bool, \"latin\": \"...\"|\"UNKNOWN\", \"russian\": \"...\"|null}."
)

_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")
_audit_lock = asyncio.Lock()
_gbif_cache: dict = {}
_counter = {"n": 0, "auto": 0, "review": 0, "skip": 0}


def ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def strong_match(card_ru, inat_ru):
    cw, iw = ruwords(card_ru), ruwords(inat_ru)
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


def russian_hint(name, llm_russian):
    """Prefer the LLM's clean Russian; else the «— Tail» of the raw name."""
    if llm_russian:
        return llm_russian
    m = re.split(r"[—–-]", name or "", maxsplit=1)
    if len(m) == 2 and ruwords(m[1]):
        return m[1].strip()
    return None


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
    if not name:
        return None
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


async def stage_finding(pid, name, title, evidence, action):
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
            "id": str(uuid.uuid4()), "cid": CHECK_ID, "eid": str(pid), "title": title,
            "ev": json.dumps(evidence, ensure_ascii=False),
            "fix": json.dumps({"action": action, "plant_id": str(pid)}, ensure_ascii=False),
        })
        await s.commit()


async def process(client, sem, row):
    pid, name, family, hist, kingdom = row
    hist = list(hist or [])
    async with sem:
        try:
            user = json.dumps({"name": name, "family": family, "aliases": hist}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=1200,
            )
        except Exception:
            llm = {}
        is_plant = llm.get("is_plant", True)
        llm_sci = (llm.get("latin") or "").strip()
        if llm_sci.upper() == "UNKNOWN":
            llm_sci = ""
        ru_hint = russian_hint(name, llm.get("russian"))
        inat = await inat_by_ru(client, ru_hint) if ru_hint else None
        inat_sci = (inat or {}).get("sci")
        g_inat = await gbif(client, inat_sci)
        g_llm = await gbif(client, llm_sci)

    if not is_plant:
        await stage_finding(pid, name, f"«{name}»: не растение (по LLM) — удалить или сменить kingdom",
                            {"name": name, "llm": llm}, "delete_or_rekingdom")
        _counter["review"] += 1
        _counter["n"] += 1
        return

    inat_ru = (inat or {}).get("ru")
    strong = strong_match(ru_hint, inat_ru)
    k_inat, k_llm = (g_inat or {}).get("usage_key"), (g_llm or {}).get("usage_key")
    agree = bool(norm_bino(inat_sci)) and (
        norm_bino(inat_sci) == norm_bino(llm_sci) or (k_inat and k_llm and k_inat == k_llm))
    # pick the GBIF-confirmed candidate: prefer iNat (truth) then LLM
    g = g_inat if (g_inat and (g_inat.get("match_type") or "").upper() in ("EXACT", "FUZZY")) else g_llm
    confirmed = g and (g.get("match_type") or "").upper() in ("EXACT", "FUZZY") and (g.get("confidence") or 0) >= 85
    has_inat = bool(inat_sci)

    if confirmed and king_ok(kingdom, g.get("kingdom")) and ((has_inat and (strong or agree)) or (not has_inat and agree)):
        canonical = g.get("canonical")
        ru_name = inat_ru or ru_hint
        async with async_session() as s:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = canonical
                if ru_name and not p.name_modern:
                    p.name_modern = ru_name
                await s.commit()
        await audit({"id": str(pid), "name": name, "action": "auto",
                     "set_latin": canonical, "set_modern": ru_name})
        _counter["auto"] += 1
    else:
        await stage_finding(pid, name,
                            f"«{name}»: имя OCR-битое, кандидат {(g or {}).get('canonical') or inat_sci or llm_sci or '—'}",
                            {"name": name, "inat": inat_sci, "inat_ru": inat_ru, "llm": llm_sci,
                             "ru_hint": ru_hint, "gbif_kingdom": (g or {}).get("kingdom")},
                            "set_latin")
        _counter["review"] += 1
    _counter["n"] += 1


async def main():
    async with async_session() as db:
        q = f"SELECT id, name, family, names_historical, kingdom FROM plants WHERE {FILTER} ORDER BY id"
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
            tail = f" ({len(errs)} errs e.g. {str(errs[0])[:60]})" if errs else ""
            print(f"... {min(i + CHUNK, len(todo))}/{len(todo)} "
                  f"(auto={_counter['auto']} review={_counter['review']}){tail}", flush=True)

    print(f"DONE n={_counter['n']} auto={_counter['auto']} review={_counter['review']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
