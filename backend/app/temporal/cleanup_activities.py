# -*- coding: utf-8 -*-
"""Durable, idempotent activities for the autonomous plant-cleanup chain
(PlantCleanupWorkflow). Each loops internally over batches and heartbeats, so a
worker restart just retries the activity and it RESUMES from the data (every step
skips already-done rows). No open session, no external watcher.

Phases: enrichment (iNat modern+photo from latin) → latin backfill (clean-Russian
latin-less species → iNat+LLM+GBIF) → enrichment again (for backfilled latins) →
rename (garbage name → name_modern, collision-safe).
"""
import json
import re
import uuid

import httpx
from sqlalchemy import text
from temporalio import activity

from app.database import async_session
from app.services.inaturalist import enrich_plants_inat, INAT_BASE, _HEADERS
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one

# ------------------------------------------------------------------ enrichment

@activity.defn
async def run_enrichment_activity() -> dict:
    """Loop enrich_plants_inat until no unsynced latin cards remain. Idempotent
    (marks each plant synced; re-run only touches the unsynced floor)."""
    resolved = names = photos = batches = 0
    while True:
        async with async_session() as db:
            r = await enrich_plants_inat(db, dry_run=False, limit=300)
        batches += 1
        resolved += r.get("taxa_resolved", 0)
        names += r.get("names_set", 0)
        photos += r.get("photos_set", 0)
        activity.heartbeat({"batch": batches, "remaining": r.get("remaining"),
                            "resolved": resolved, "names": names, "photos": photos})
        if not r.get("processed"):
            break
    return {"phase": "enrichment", "batches": batches, "resolved": resolved,
            "names": names, "photos": photos}


# ------------------------------------------------------------------ backfill

_BF_FILTER = r"""
    name_latin IS NULL
    AND name ~ '[А-Яа-яЁё]' AND name !~ '[A-Za-z0-9{}\[\]\$<>|@]'
    AND array_length(regexp_split_to_array(btrim(name),'\s+'),1) >= 2
"""
_BF_CHECK = "identity.latin_backfill"
_BF_SYS = (
    "Ты ботаник-таксономист. Дан JSON карточки растения: name (русский бином вида), "
    "family, aliases. Определи: 1) is_plant true/false (false для веществ/препаратов/"
    "сырья); 2) latin — ПРИНЯТЫЙ научный бином (род+вид) или \"UNKNOWN\"; 3) russian — "
    "чистое современное русское название или null. Строго JSON: "
    "{\"is_plant\": bool, \"latin\": \"Genus species\"|\"UNKNOWN\", \"russian\": \"...\"|null}."
)
_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")


def _ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def _norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def _strong(card, ru):
    cw, iw = _ruwords(card), _ruwords(ru)
    if not cw or not iw or iw[0] != cw[0]:
        return False
    return (len(cw) == 1 and len(iw) == 1) or cw[-1] == iw[-1]


def _king_ok(card_kingdom, gk):
    if not gk:
        return False
    return gk == "Fungi" if (card_kingdom or "").startswith("гриб") else gk in ("Plantae", "Chromista")


async def _inat_by_ru(client, name, cache):
    if name in cache:
        return cache[name]
    out = None
    qs = [name]
    w = _ruwords(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    for q in qs:
        try:
            resp = await client.get(f"{INAT_BASE}/taxa",
                                    params={"q": q, "locale": "ru", "per_page": 5, "is_active": "true"},
                                    headers=_HEADERS)
            if resp.status_code == 429:
                continue
            if resp.status_code != 200:
                break
            sp = [r for r in resp.json().get("results", []) if r.get("rank") == "species"]
            if sp:
                out = {"sci": sp[0].get("name"), "ru": sp[0].get("preferred_common_name")}
                break
        except Exception:
            break
    cache[name] = out
    return out


async def _gbif(client, sci, cache):
    if not sci:
        return None
    k = _norm_bino(sci)
    if k in cache:
        return cache[k]
    g = await _resolve_one(client, sci)
    cache[k] = g
    return g


@activity.defn
async def run_backfill_activity() -> dict:
    """Backfill name_latin (+ name_modern, + reset inat_synced_at so the next
    enrichment grabs photos) for latin-less cards with a clean Russian binomial.
    Auto-writes when iNat+GBIF confirm (strong ru-match OR iNat/LLM agree); else a
    review finding. Idempotent: auto/non-plant/review all drop out of the todo on
    re-run (latin set OR finding staged)."""
    gbif_cache: dict = {}
    auto = review = nonplant = processed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            # Exclude already-staged review/non-plant rows IN SQL — otherwise the
            # LIMIT window stays stuck on them (they keep matching the latin-IS-NULL
            # filter) and never reaches the unprocessed tail. Auto-written rows drop
            # out of the filter on their own (latin set).
            async with async_session() as db:
                todo = (await db.execute(text(
                    f"SELECT id, name, family, names_historical, kingdom FROM plants WHERE {_BF_FILTER} "
                    f"AND id::text NOT IN (SELECT entity_id FROM data_quality_findings WHERE check_id=:c) "
                    f"ORDER BY id LIMIT 100"), {"c": _BF_CHECK})).all()
            if not todo:
                break
            inat_cache: dict = {}
            for pid, name, family, hist, kingdom in todo:
                try:
                    user = json.dumps({"name": name, "family": family, "aliases": list(hist or [])}, ensure_ascii=False)
                    llm = await chat_completion_json(
                        [{"role": "system", "content": _BF_SYS}, {"role": "user", "content": user}],
                        task="plant_extraction", temperature=0.1, max_tokens=900)
                except Exception:
                    llm = {}
                is_plant = llm.get("is_plant", True)
                llm_sci = (llm.get("latin") or "").strip()
                if llm_sci.upper() == "UNKNOWN":
                    llm_sci = ""
                if not is_plant:
                    await _stage(pid, name, f"«{name}»: не растение (LLM) — удалить/сменить kingdom",
                                 "delete_or_rekingdom", {"name": name}, _BF_CHECK)
                    nonplant += 1; processed += 1
                    continue
                inat = await _inat_by_ru(client, name, inat_cache)
                inat_sci = (inat or {}).get("sci")
                g_inat = await _gbif(client, inat_sci, gbif_cache)
                g_llm = await _gbif(client, llm_sci, gbif_cache)
                inat_ru = (inat or {}).get("ru")
                k_i, k_l = (g_inat or {}).get("usage_key"), (g_llm or {}).get("usage_key")
                agree = bool(_norm_bino(inat_sci)) and (
                    _norm_bino(inat_sci) == _norm_bino(llm_sci) or (k_i and k_l and k_i == k_l))
                g = g_inat if (g_inat and (g_inat.get("match_type") or "").upper() in ("EXACT", "FUZZY")) else g_llm
                confirmed = g and (g.get("match_type") or "").upper() in ("EXACT", "FUZZY") and (g.get("confidence") or 0) >= 85
                if inat_sci and confirmed and _king_ok(kingdom, g.get("kingdom")) and (_strong(name, inat_ru) or agree):
                    async with async_session() as db:
                        await db.execute(text(
                            "UPDATE plants SET name_latin=:l, name_modern=COALESCE(name_modern,:m), inat_synced_at=NULL WHERE id=:id"),
                            {"l": g.get("canonical"), "m": inat_ru, "id": str(pid)})
                        await db.commit()
                    auto += 1
                else:
                    await _stage(pid, name, f"«{name}»: латынь не заполнена, кандидат {(g or {}).get('canonical') or inat_sci or llm_sci or '—'}",
                                 "set_latin", {"name": name, "inat": inat_sci, "inat_ru": inat_ru, "llm": llm_sci}, _BF_CHECK)
                    review += 1
                processed += 1
            activity.heartbeat({"processed": processed, "auto": auto, "review": review, "nonplant": nonplant})
    return {"phase": "backfill", "processed": processed, "auto": auto, "review": review, "nonplant": nonplant}


async def _stage(pid, name, title, action, ev, check_id):
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:fix AS jsonb), false, 'open', now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              title=EXCLUDED.title, evidence=EXCLUDED.evidence, suggested_fix=EXCLUDED.suggested_fix, last_seen=now()
        """), {"id": str(uuid.uuid4()), "cid": check_id, "eid": str(pid), "title": title,
               "ev": json.dumps(ev, ensure_ascii=False),
               "fix": json.dumps({"action": action, "plant_id": str(pid)}, ensure_ascii=False)})
        await db.commit()


# ------------------------------------------------------------------ rename

@activity.defn
async def run_rename_activity() -> dict:
    """name = name_modern where the name is garbage (digit/junk/mixed/abbrev) and
    name_modern is set — BUT only when no OTHER card already holds that name
    (collisions = duplicates → left for the later merge phase). Idempotent."""
    G = ("(p.name ~ '[0-9]' OR p.name ~ '[@{}\\[\\]\\$<>|]' OR "
         "(p.name ~ '[A-Za-z]' AND p.name ~ '[А-Яа-яЁё]') OR p.name ~ '^[А-ЯЁ]\\.')")
    NC = "NOT EXISTS (SELECT 1 FROM plants o WHERE o.id<>p.id AND lower(o.name)=lower(p.name_modern))"
    async with async_session() as db:
        res = await db.execute(text(
            f"WITH t AS (SELECT p.id FROM plants p WHERE p.name_modern IS NOT NULL AND p.name <> p.name_modern AND {G} AND {NC}) "
            f"UPDATE plants SET name = name_modern FROM t WHERE plants.id = t.id"))
        await db.commit()
    return {"phase": "rename", "renamed": res.rowcount}
