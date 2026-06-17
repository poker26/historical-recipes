"""Re-ID pass for rich-but-broken cards: OCR-garbled name/latin BUT real content
(compounds / uses / toxicity). Feed the LLM the FULL context (name, latin, family,
description, chemistry, actions) — the garbled latin usually encodes the binomial,
family/description/chemistry corroborate. GBIF verifies (kingdom gate). Confirmed →
set name_latin + Russian name + unsync for photo; else stage a review finding.

Usage:  python reid_run.py dry [N]   — sample, print proposals, NO writes
        python reid_run.py           — full run, apply confirmed + stage the rest
"""
import asyncio
import json
import re
import sys

import httpx
from sqlalchemy import text

from app.database import async_session
from app.services import llm as _llm
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one

JUNK = re.compile(r"[\$@{}\[\]|<>0-9!]")
CYR = re.compile(r"[А-Яа-яЁё]")
LAT = re.compile(r"[A-Za-z]")
CLEAN_BINO = re.compile(r"^[A-Z][a-z]{2,}\s+[a-z]{2,}")
CLEAN_GENUS = re.compile(r"^[A-Z][a-z]{2,}\.?$")
ABBREV = re.compile(r"^[А-ЯA-Z]\.\s*\S")
JUNK_BUCKETS = {"ocr_junk", "abbrev_genus", "mixed_script", "other_messy"}
CHECK = "identity.reid_broken"

_SYS = (
    "Ты ботаник-систематик. Карточка растения из старого определителя/флоры имеет "
    "OCR-ИСПОРЧЕННОЕ имя и латынь, но семейство, описание и химсостав обычно ВЕРНЫ. "
    "Восстанови ПРИНЯТЫЙ научный бином (род вид), который имелся в виду. Битая латынь "
    "часто кодирует его (кириллица-как-латынь: $→S, Г,.→L., и→u, Б→b; ПЕРВАЯ буква "
    "тоже могла исказиться: Д→О и т.п.). ЖЁСТКИЕ ПРАВИЛА: "
    "(1) ЛАТИНСКИЙ АВТОР в имени сильно ограничивает род — «C. Chr.» = Christensen "
    "(почти всегда папоротники, особ. Dryopteris); «DC.», «Bunge», «Stev.» и т.п. — "
    "учитывай его. "
    "(2) ПРОВЕРЬ согласие: предложенный род ОБЯЗАН биться с семейством И местообитанием "
    "из описания. Степное/луговое бобовое (Oxytropis) НЕ растёт «в хвойных лесах»; "
    "если род противоречит описанию/семейству — это НЕ он, ищи другой. "
    "(3) Химия — подсказка (хромоны/флороглюцины → папоротники Dryopteris). "
    "(4) НЕ УГАДЫВАЙ вид: если уверенно читается только РОД, верни один род и "
    "confidence ≤ 60. UNKNOWN — если и род ненадёжен. "
    "Строго JSON: {\"latin\":\"Genus species\"|\"Genus\"|\"UNKNOWN\","
    "\"russian\":\"русское название\"|null,\"confidence\":0-100,\"reason\":\"кратко\"}."
)


def name_bucket(name):
    n = (name or "").strip()
    if JUNK.search(n):
        return "ocr_junk"
    if ABBREV.match(n):
        return "abbrev_genus"
    if not CYR.search(n) and CLEAN_BINO.match(n):
        return "clean_latin_binomial"
    if not CYR.search(n) and CLEAN_GENUS.match(n):
        return "clean_latin_genus"
    if CYR.search(n) and LAT.search(n):
        return "mixed_script"
    return "other_messy"


def _king_ok(card_kingdom, gk):
    if not gk:
        return False
    return gk == "Fungi" if (card_kingdom or "").startswith("гриб") else gk in ("Plantae", "Chromista")


async def _gbif_retry(client, sci, tries=3):
    """_resolve_one with backoff — GBIF is intermittently flaky; a transient None
    must not dump a salvageable card into review."""
    for k in range(tries):
        g = await _resolve_one(client, sci)
        if g and g.get("match_type"):
            return g
        await asyncio.sleep(1.2 * (k + 1))
    return None


async def _db_has_genus(genus):
    """True if our herbarium already holds CLEAN cards of this genus — a local,
    GBIF-independent confirmation that the genus is real and in-scope (a plant)."""
    async with async_session() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM plants WHERE name_latin ILIKE :g AND name_latin ~ '^[A-Z][a-z]+ [a-z]+'"),
            {"g": genus + " %"})).scalar()
    return (n or 0) >= 1


async def fetch_targets(s, limit=None, only_ids=None):
    q = r"""
        SELECT p.id, p.name, p.name_latin, p.family, p.kingdom, p.description,
          (SELECT string_agg(DISTINCT c.name, ', ') FROM plant_compounds pc JOIN compounds c ON c.id=pc.compound_id WHERE pc.plant_id=p.id) comps,
          (SELECT string_agg(DISTINCT u.action_raw, ', ') FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_raw IS NOT NULL) acts,
          (SELECT count(*) FROM plant_compounds c WHERE c.plant_id=p.id) nc,
          (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_id IS NOT NULL) nru,
          (SELECT count(*) FROM plant_toxicities t WHERE t.plant_id=p.id) ntox
        FROM plants p
        WHERE (p.name ~ '[A-Za-z]' OR p.name ~ '[\$@{}\[\]|<>]' OR p.name ~ '^[А-ЯA-Z]\.' OR p.name ~ '[0-9]')
          AND p.id::text NOT IN (SELECT entity_id FROM data_quality_findings WHERE check_id=:c AND status='resolved')
    """
    rows = (await s.execute(text(q), {"c": CHECK})).all()
    out = []
    for r in rows:
        if only_ids and str(r[0]) not in only_ids:
            continue
        if name_bucket(r[1]) not in JUNK_BUCKETS:
            continue
        nc, nru, ntox = r[8], r[9], r[10]
        if not (nc or nru or ntox):   # rich-but-broken only
            continue
        out.append(r)
        if limit and len(out) >= limit:
            break
    return out


async def reid_one(client, r):
    """LLM (sharpened prompt) → GBIF verify → a decision dict.
    action ∈ apply_species | apply_genus | review."""
    pid, name, latin, family, kingdom, desc, comps, acts = r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]
    payload = json.dumps({
        "name": name, "name_latin": latin, "family": family,
        "description": (desc or "")[:300],
        "compounds": (comps or "")[:200], "actions": (acts or "")[:200],
    }, ensure_ascii=False)
    try:
        llm = await chat_completion_json(
            [{"role": "system", "content": _SYS}, {"role": "user", "content": payload}],
            task="plant_extraction", temperature=0.1, max_tokens=600)
    except Exception as e:
        return {"pid": str(pid), "name": name, "action": "error", "reason": str(e)[:80]}
    sci = (llm.get("latin") or "").strip()
    russian = (llm.get("russian") or "").strip() or None
    conf = llm.get("confidence") or 0
    out = {"pid": str(pid), "name": name, "proposed": sci, "russian": russian,
           "conf": conf, "reason": llm.get("reason")}
    if not sci or sci.upper() == "UNKNOWN":
        out["action"] = "review"
        return out
    is_binomial = len(sci.split()) >= 2
    genus = sci.split()[0]
    g = await _gbif_retry(client, sci)
    canonical = (g or {}).get("canonical")
    gmatch = bool(g and (g.get("match_type") or "").upper() in ("EXACT", "FUZZY"))
    gconf = (g or {}).get("confidence") or 0
    kok = _king_ok(kingdom, (g or {}).get("kingdom"))
    out.update({"gbif": canonical, "gbif_match": (g or {}).get("match_type"), "gbif_kingdom": (g or {}).get("kingdom")})
    if is_binomial and gmatch and gconf >= 85 and kok and conf >= 70:
        out["action"], out["latin"] = "apply_species", canonical
        return out
    # else fall to GENUS level — confirm the genus via GBIF (retry) or our own DB
    gg = g if (not is_binomial) else await _gbif_retry(client, genus)
    if gg and (gg.get("match_type") or "").upper() in ("EXACT", "FUZZY") and _king_ok(kingdom, gg.get("kingdom")):
        out["action"], out["latin"] = "apply_genus", (gg.get("canonical") or genus)
    elif conf >= 50 and await _db_has_genus(genus):
        out["action"], out["latin"], out["via"] = "apply_genus", genus, "local_db"
    else:
        out["action"] = "review"
    return out


async def _record(pid, status, evidence):
    async with async_session() as s:
        await s.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (gen_random_uuid(), :c, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:ev AS jsonb), false, :st, now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              title=EXCLUDED.title, evidence=EXCLUDED.evidence, status=EXCLUDED.status, last_seen=now()
        """), {"c": CHECK, "eid": pid, "title": (evidence.get("name") or "")[:120],
               "ev": json.dumps(evidence, ensure_ascii=False), "st": status})
        await s.commit()


async def _apply(pid, latin, russian):
    """Set name_latin (recovered, GBIF/DB-confirmed) and put the CLEAN latin into
    `name` (replacing the OCR garbage). The LLM's Russian goes to name_modern only —
    it is unreliable («рогооз Бюффона»), so it must NOT land in `name`. Reset
    inat_synced_at so enrichment resolves the now-correct latin and promotes the
    AUTHORITATIVE iNat Russian name into `name` (bare-latin promotion rule)."""
    async with async_session() as s:
        await s.execute(text(
            "UPDATE plants SET name_latin=:l, name=:l, "
            "name_modern=COALESCE(:r, name_modern), inat_synced_at=NULL "
            "WHERE id=CAST(:i AS uuid)"),
            {"l": latin, "r": russian, "i": pid})
        await s.commit()


async def main():
    dry = len(sys.argv) > 1 and sys.argv[1] == "dry"
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)  # optional LIMIT, any position
    model = next((a for a in sys.argv[1:] if "/" in a), None)
    if model:
        _llm.MODELS["plant_extraction"] = model
        print(f"MODEL OVERRIDE: {model}")
    SAMPLE = {"3d847ba9-8358-41b3-9df0-91facf339401", "eed6579f-1d98-45fa-b25b-4989ba6b66af"}
    async with async_session() as s:
        targets = await fetch_targets(s, limit=n, only_ids=SAMPLE if dry else None)
    print(f"targets: {len(targets)} (dry={dry})", flush=True)
    species = genus = review = err = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i, r in enumerate(targets):
            res = await reid_one(client, r)
            act = res.get("action")
            if dry:
                print(json.dumps(res, ensure_ascii=False))
                continue
            if act == "apply_species":
                await _apply(res["pid"], res["latin"], res["russian"]); species += 1
                await _record(res["pid"], "resolved", res)
            elif act == "apply_genus":
                await _apply(res["pid"], res["latin"], res["russian"]); genus += 1
                await _record(res["pid"], "resolved", res)
            elif act == "error":
                err += 1   # transient — no finding, retried on the next run
            else:
                await _record(res["pid"], "open", res); review += 1
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(targets)}: species={species} genus={genus} review={review} err={err}", flush=True)
    print(f"DONE. species={species} genus={genus} review={review} err={err}", flush=True)


asyncio.run(main())
