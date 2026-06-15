# -*- coding: utf-8 -*-
"""LATIN BACKFILL for the ~5385 latin-LESS cards that have a CLEAN Russian binomial
name (Молочай блестящий, Осока ранняя, Валериана лекарственная …) — surfaced by
identity.headnoun_cluster. Our earlier passes only touched GARBLED names; these have
good names and were skipped, yet they lack name_latin. Clean names resolve with a
HIGHER hit-rate than the OCR cases.

Per card (same validated gate): iNat by the Russian name (primary) + LLM (qwen3-235b)
propose; GBIF normalizes both, kingdom must match the card; auto when GBIF-confirmed
AND (iNat ru-name strong-matches OR iNat/LLM agree). Auto -> name_latin = canonical,
name_modern = iNat ru name. is_plant=false (Поваренная соль, Минеральное масло) ->
flag delete_or_rekingdom. Else -> review (identity.latin_backfill). Chunked, resumable.
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
CHECK_ID = "identity.latin_backfill"
AUDIT = "/tmp/backfill_latin_audit.jsonl"

# latin-less + clean Russian multi-word name (no Latin/digit/junk).
FILTER = r"""
    name_latin IS NULL
    AND name ~ '[А-Яа-яЁё]' AND name !~ '[A-Za-z0-9{}@\[\]\$<>|]'
    AND array_length(regexp_split_to_array(btrim(name),'\s+'),1) >= 2
"""

SYS = (
    "Ты ботаник-таксономист. Дан JSON карточки растения из русского источника: name "
    "(русский бином вида), family, aliases. Определи:\n"
    "1) is_plant: true/false (false для веществ/препаратов/сырья: «Поваренная соль», "
    "«Минеральное масло», «Соляная кислота»);\n"
    "2) latin: ПРИНЯТЫЙ научный бином (род+вид) для этого вида, иначе \"UNKNOWN\". Не выдумывай;\n"
    "3) russian: чистое современное русское название (обычно = name), иначе null.\n"
    "Строго JSON: {\"is_plant\": bool, \"latin\": \"Genus species\"|\"UNKNOWN\", \"russian\": \"...\"|null}."
)

_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")
_audit_lock = asyncio.Lock()
_gbif_cache: dict = {}
_counter = {"n": 0, "auto": 0, "review": 0, "nonplant": 0}


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


async def stage_finding(pid, name, title, action, ev):
    async with async_session() as s:
        await s.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:fix AS jsonb), false, 'open', now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              title=EXCLUDED.title, evidence=EXCLUDED.evidence, suggested_fix=EXCLUDED.suggested_fix, last_seen=now()
        """), {"id": str(uuid.uuid4()), "cid": CHECK_ID, "eid": str(pid), "title": title,
               "ev": json.dumps(ev, ensure_ascii=False),
               "fix": json.dumps({"action": action, "plant_id": str(pid)}, ensure_ascii=False)})
        await s.commit()


async def process(client, sem, row):
    pid, name, family, hist, kingdom = row
    async with sem:
        try:
            user = json.dumps({"name": name, "family": family, "aliases": list(hist or [])}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=900,
            )
        except Exception:
            llm = {}
        is_plant = llm.get("is_plant", True)
        llm_sci = (llm.get("latin") or "").strip()
        if llm_sci.upper() == "UNKNOWN":
            llm_sci = ""
        inat = await inat_by_ru(client, name)
        inat_sci = (inat or {}).get("sci")
        g_inat = await gbif(client, inat_sci)
        g_llm = await gbif(client, llm_sci)

    if not is_plant:
        await stage_finding(pid, name, f"«{name}»: не растение (по LLM) — удалить/сменить kingdom", "delete_or_rekingdom", {"name": name})
        _counter["nonplant"] += 1; _counter["n"] += 1
        return

    inat_ru = (inat or {}).get("ru")
    strong = strong_match(name, inat_ru)
    k_inat, k_llm = (g_inat or {}).get("usage_key"), (g_llm or {}).get("usage_key")
    agree = bool(norm_bino(inat_sci)) and (norm_bino(inat_sci) == norm_bino(llm_sci) or (k_inat and k_llm and k_inat == k_llm))
    g = g_inat if (g_inat and (g_inat.get("match_type") or "").upper() in ("EXACT", "FUZZY")) else g_llm
    confirmed = g and (g.get("match_type") or "").upper() in ("EXACT", "FUZZY") and (g.get("confidence") or 0) >= 85
    has_inat = bool(inat_sci)

    if confirmed and king_ok(kingdom, g.get("kingdom")) and ((has_inat and (strong or agree)) or (not has_inat and agree)):
        canonical = g.get("canonical")
        async with async_session() as s:
            p = await s.get(Plant, uuid.UUID(str(pid)))
            if p:
                p.name_latin = canonical
                if inat_ru and not p.name_modern:
                    p.name_modern = inat_ru
                await s.commit()
        await audit({"id": str(pid), "name": name, "action": "auto", "set_latin": canonical, "set_modern": inat_ru})
        _counter["auto"] += 1
    else:
        await stage_finding(pid, name, f"«{name}»: латынь не заполнена, кандидат {(g or {}).get('canonical') or inat_sci or llm_sci or '—'}",
                            "set_latin", {"name": name, "inat": inat_sci, "inat_ru": inat_ru, "llm": llm_sci, "gbif_kingdom": (g or {}).get("kingdom")})
        _counter["review"] += 1
    _counter["n"] += 1


async def main():
    async with async_session() as db:
        q = f"SELECT id, name, family, names_historical, kingdom FROM plants WHERE {FILTER} ORDER BY id"
        if LIMIT:
            q += f" LIMIT {LIMIT}"
        rows = (await db.execute(text(q))).all()
        done = {e for (e,) in (await db.execute(select(DataQualityFinding.entity_id).where(DataQualityFinding.check_id == CHECK_ID))).all()}
    todo = [r for r in rows if str(r[0]) not in done]
    print(f"candidates={len(rows)} already_staged={len(rows)-len(todo)} todo={len(todo)}", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            res = await asyncio.gather(*[process(client, sem, r) for r in chunk], return_exceptions=True)
            errs = [r for r in res if isinstance(r, Exception)]
            tail = f" ({len(errs)} errs e.g. {str(errs[0])[:60]})" if errs else ""
            print(f"... {min(i+CHUNK,len(todo))}/{len(todo)} (auto={_counter['auto']} review={_counter['review']} nonplant={_counter['nonplant']}){tail}", flush=True)
    print(f"DONE n={_counter['n']} auto={_counter['auto']} review={_counter['review']} nonplant={_counter['nonplant']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
