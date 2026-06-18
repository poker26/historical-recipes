# -*- coding: utf-8 -*-
"""Durable, idempotent activities for the autonomous plant-cleanup chain
(PlantCleanupWorkflow). Each loops internally over batches and heartbeats, so a
worker restart just retries the activity and it RESUMES from the data (every step
skips already-done rows). No open session, no external watcher.

Phases: enrichment (iNat modern+photo from latin) → latin backfill (clean-Russian
latin-less species → iNat+LLM+GBIF) → enrichment again (for backfilled latins) →
rename (garbage name → name_modern, collision-safe).
"""
import asyncio
import json
import re
import uuid

import httpx
from sqlalchemy import text
from temporalio import activity

from app.database import async_session
from app.services.inaturalist import enrich_plants_inat, INAT_BASE, _HEADERS
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one, GBIF_MATCH_URL

# ------------------------------------------------------------------ enrichment

@activity.defn
async def run_enrichment_activity() -> dict:
    """Loop enrich_plants_inat until no unsynced latin cards remain. Idempotent
    (marks each plant synced; re-run only touches the unsynced floor).

    Heartbeats PER PLANT (progress callback) so a 429-heavy batch can't blow the
    15-min activity heartbeat-timeout and trigger the retry-storm that hammers iNat
    and starves the live walk (the 2026-06-15 jam). Paced 3s/req + 150/batch keeps
    each DB session under the 10-min idle-in-tx timeout and leaves iNat headroom for
    the consumer walk; a pure-throttle batch (iNat pushing back) BREAKS instead of
    re-hammering the same unsynced floor."""
    resolved = names = photos = batches = 0

    def _hb(done, total, name):
        activity.heartbeat({"batch": batches + 1, "plant": done, "of": total,
                            "name": name, "resolved": resolved, "names": names})

    while True:
        async with async_session() as db:
            r = await enrich_plants_inat(db, dry_run=False, limit=150,
                                         pace_seconds=3.0, progress=_hb)
        batches += 1
        resolved += r.get("taxa_resolved", 0)
        names += r.get("names_set", 0)
        photos += r.get("photos_set", 0)
        activity.heartbeat({"batch": batches, "remaining": r.get("remaining"),
                            "resolved": resolved, "names": names, "photos": photos})
        if not r.get("processed"):
            break
        # iNat pushing back (whole batch throttled, nothing resolved/no-matched) →
        # stop rather than spin on the same floor and starve the live walk.
        if (r.get("taxa_resolved", 0) == 0 and r.get("no_match", 0) == 0
                and r.get("throttled", 0) > 0):
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
                # Heartbeat per plant (after the slow LLM call) so a batch of slow
                # LLM/iNat calls can't exceed the 15-min heartbeat-timeout.
                activity.heartbeat({"processed": processed, "auto": auto,
                                    "review": review, "nonplant": nonplant})
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


# ------------------------------------------------------------------ fill latin
# Resolve name_latin for cards with a CLEAN Russian name but NULL latin (40% of the
# herbarium → invisible to the latin-keyed dedup). qwen3-235b TRANSLATES the russian
# binomial → latin binomial (джунгарский→soongaricum); GBIF verifies (retry) with a
# local-DB genus fallback. Sets ONLY name_latin (keeps the Russian name) + unsync.
_FILL_CHECK = "identity.fill_latin"
_FILL_SYS = (
    "Ты ботаник-систематик. Дано ЧИСТОЕ русское название растения (+ опц. семейство/"
    "описание/химия). Русское название обычно содержит И РОД, И ВИДОВОЙ ЭПИТЕТ — "
    "переведи его в ПРИНЯТЫЙ научный БИНОМ: «Аконит джунгарский»→Aconitum soongaricum, "
    "«Аконит байкальский»→Aconitum baicalense, «Борец Кузнецова»→Aconitum kusnezoffii. "
    "Семейство/описание/химия — для снятия ОМОНИМИИ. ВЕРНИ ВИД, когда эпитет определим "
    "(это перевод, не угадывание); только если видовой эпитет реально неоднозначен — "
    "верни один РОД (confidence ≤ 60). UNKNOWN, если и род ненадёжен. Строго JSON: "
    "{\"latin\":\"Genus species\"|\"Genus\"|\"UNKNOWN\",\"confidence\":0-100,\"reason\":\"кратко\"}."
)
_FILL_BASE = (r"name_latin IS NULL AND name ~ '[А-Яа-яЁё]' AND name !~ '[A-Za-z0-9]' "
              r"AND array_length(regexp_split_to_array(btrim(name),'\s+'),1) >= 2")


async def _gbif_retry(client, sci, tries=3):
    for k in range(tries):
        g = await _resolve_one(client, sci)
        if g and g.get("match_type"):
            return g
        await asyncio.sleep(1.2 * (k + 1))
    return None


async def _db_has_genus(genus):
    async with async_session() as db:
        n = (await db.execute(text(
            "SELECT count(*) FROM plants WHERE name_latin ILIKE :g AND name_latin ~ '^[A-Z][a-z]+ [a-z]+'"),
            {"g": genus + " %"})).scalar()
    return (n or 0) >= 1


async def _fill_decide(client, name, family, kingdom, desc, comps, acts):
    payload = json.dumps({"name": name, "family": family, "description": (desc or "")[:300],
                          "compounds": (comps or "")[:200], "actions": (acts or "")[:200]}, ensure_ascii=False)
    try:
        llm = await chat_completion_json(
            [{"role": "system", "content": _FILL_SYS}, {"role": "user", "content": payload}],
            task="plant_extraction", temperature=0.1, max_tokens=500)
    except Exception:
        return {"action": "error", "name": name}
    sci = (llm.get("latin") or "").strip()
    conf = llm.get("confidence") or 0
    out = {"name": name, "proposed": sci, "conf": conf, "reason": llm.get("reason")}
    if not sci or sci.upper() == "UNKNOWN":
        out["action"] = "review"
        return out
    is_bino = len(sci.split()) >= 2
    genus = sci.split()[0]
    g = await _gbif_retry(client, sci)
    canonical = (g or {}).get("canonical")
    gmatch = bool(g and (g.get("match_type") or "").upper() in ("EXACT", "FUZZY"))
    kok = _king_ok(kingdom, (g or {}).get("kingdom"))
    out["gbif"] = canonical
    if is_bino and gmatch and (g.get("confidence") or 0) >= 85 and kok and conf >= 70:
        out["action"], out["latin"] = "apply_species", canonical
        return out
    gg = g if not is_bino else await _gbif_retry(client, genus)
    if gg and (gg.get("match_type") or "").upper() in ("EXACT", "FUZZY") and _king_ok(kingdom, gg.get("kingdom")):
        out["action"], out["latin"] = "apply_genus", (gg.get("canonical") or genus)
    elif conf >= 50 and await _db_has_genus(genus):
        out["action"], out["latin"] = "apply_genus", genus
    else:
        out["action"] = "review"
    return out


@activity.defn
async def biotope_canon_activity() -> dict:
    """Durable biotope normalization: canonicalize free-text plant_habitats.biotope
    into controlled biotopes (plant_biotopes). Heartbeats per plant; idempotent —
    skips plants already in plant_biotopes, and a NULL-biotope row marks a 0-tag
    plant as processed → resumes on restart, guaranteed termination. (Was a fragile
    detached script that died on every dispatcher rebuild.)"""
    from app.services.biotope import canonicalize
    done = tagged = empty = 0
    sem = asyncio.Semaphore(10)   # 10 concurrent LLM calls (was strictly sequential)

    async def _canon(pid, bio):
        async with sem:
            # per-call timeout: a single hung httpx request (timeout=600) must NOT
            # block the whole gather batch — cap it, treat a hang as «no tags».
            try:
                tags = await asyncio.wait_for(canonicalize(bio), timeout=40)
            except Exception:
                tags = []
            return str(pid), tags

    while True:
        async with async_session() as db:
            rows = (await db.execute(text("""
                SELECT h.plant_id, string_agg(DISTINCT h.biotope, ' | ') AS bio
                FROM plant_habitats h
                WHERE h.biotope IS NOT NULL AND length(h.biotope) > 3
                  AND h.plant_id NOT IN (SELECT plant_id FROM plant_biotopes)
                GROUP BY h.plant_id LIMIT 100
            """))).all()
        if not rows:
            break
        results = await asyncio.gather(*[_canon(pid, bio) for pid, bio in rows])
        for pid, tags in results:
            async with async_session() as db:
                if tags:
                    for t in tags:
                        await db.execute(text(
                            "INSERT INTO plant_biotopes (plant_id, biotope) VALUES (:p, :b)"),
                            {"p": pid, "b": t})
                    tagged += 1
                else:
                    await db.execute(text(
                        "INSERT INTO plant_biotopes (plant_id, biotope) VALUES (:p, NULL)"),
                        {"p": pid})
                    empty += 1
                await db.commit()
            done += 1
            activity.heartbeat({"done": done, "tagged": tagged, "empty": empty})
    return {"phase": "biotope", "done": done, "tagged": tagged, "empty": empty}


@activity.defn
async def fill_latin_activity() -> dict:
    """Durable, idempotent NULL-latin fill. Loops batches of clean-Russian-name NULL-
    latin cards, heartbeating per card so a slow LLM/GBIF batch can't blow the timeout;
    resumes on worker restart. EVERY card processed gets a finding (resolved on a fix,
    open on review/error) and the todo excludes any card already holding a fill finding
    → guaranteed termination (no infinite loop on review/error cards)."""
    species = genus = review = err = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            async with async_session() as db:
                rows = (await db.execute(text(
                    f"SELECT p.id, p.name, p.family, p.kingdom, p.description, "
                    f"(SELECT string_agg(DISTINCT c.name, ', ') FROM plant_compounds pc JOIN compounds c ON c.id=pc.compound_id WHERE pc.plant_id=p.id), "
                    f"(SELECT string_agg(DISTINCT u.action_raw, ', ') FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_raw IS NOT NULL) "
                    f"FROM plants p WHERE {_FILL_BASE} "
                    f"AND p.id::text NOT IN (SELECT entity_id FROM data_quality_findings WHERE check_id=:c) "
                    f"ORDER BY p.id LIMIT 100"), {"c": _FILL_CHECK})).all()
            if not rows:
                break
            for pid, name, family, kingdom, desc, comps, acts in rows:
                r = await _fill_decide(client, name, family, kingdom, desc, comps, acts)
                act = r.get("action")
                if act in ("apply_species", "apply_genus"):
                    async with async_session() as db:
                        await db.execute(text(
                            "UPDATE plants SET name_latin=:l, inat_synced_at=NULL WHERE id=:i"),
                            {"l": r["latin"], "i": pid})
                        await db.commit()
                    await _stage(pid, name, f"«{name}» → {r['latin']}", "fill_latin", r, _FILL_CHECK)
                    # mark the finding resolved (fix applied)
                    async with async_session() as db:
                        await db.execute(text(
                            "UPDATE data_quality_findings SET status='resolved' WHERE check_id=:c AND entity_id=:e"),
                            {"c": _FILL_CHECK, "e": str(pid)})
                        await db.commit()
                    species += act == "apply_species"
                    genus += act == "apply_genus"
                else:
                    await _stage(pid, name, f"«{name}»: латынь не определена ({act})", "review", r, _FILL_CHECK)
                    review += act == "review"
                    err += act == "error"
                activity.heartbeat({"species": species, "genus": genus, "review": review, "err": err})
    return {"phase": "fill_latin", "species": species, "genus": genus, "review": review, "err": err}


# ------------------------------------------------------------------ identity conflict
# name↔latin corruption: a card whose clean Russian name resolves (iNat) to a
# DIFFERENT genus than its stored latin («Кирказон» / Aconitum vulparia). Distinguish
# real corruption from a TAXONOMIC SYNONYM (Festuca↔Lolium) via the GBIF speciesKey:
# same accepted species = reclassification, NOT corruption → skip.
_CONFLICT_CHECK = "identity.conflict"


def _genus_of(latin):
    t = _LAT.findall(latin or "")
    return t[0].lower() if t else None


async def _gbif_full(client, sci, cache):
    if not sci:
        return None
    if sci in cache:
        return cache[sci]
    out = None
    try:
        r = await client.get(GBIF_MATCH_URL, params={"name": sci})
        d = r.json()
        if d.get("matchType") and d.get("matchType") != "NONE":
            out = {"speciesKey": d.get("speciesKey"),
                   "canonical": d.get("canonicalName") or d.get("scientificName"),
                   "kingdom": d.get("kingdom"), "match": d.get("matchType")}
    except Exception:
        out = None
    cache[sci] = out
    return out


@activity.defn
async def conflict_check_activity() -> dict:
    """Durable name↔latin conflict fix. Cursor-resumable via heartbeat (last_id) so a
    worker restart continues from where it left off; only ACTUAL conflicts get a
    finding (agree/no-match/synonym leave none). gbif-confirmed corruption → fix the
    latin (the Russian name is the grounded source); uncertain → review."""
    info = activity.info()
    last_id = ""
    if info.heartbeat_details:
        try:
            last_id = info.heartbeat_details[0].get("last_id", "") or ""
        except Exception:
            last_id = ""
    fixed = review = agree = nomatch = synonym = 0
    gcache: dict = {}
    async with httpx.AsyncClient(timeout=30) as client:
        icache: dict = {}
        while True:
            async with async_session() as db:
                rows = (await db.execute(text(
                    "SELECT id, name, name_latin, kingdom FROM plants "
                    "WHERE name ~ '[А-Яа-яЁё]' AND name_latin ~ '^[A-Za-z]+ [a-z]+' "
                    "AND id::text > :cur "
                    "AND id::text NOT IN (SELECT entity_id FROM data_quality_findings WHERE check_id=:c AND status='resolved') "
                    "ORDER BY id::text LIMIT 100"), {"cur": last_id, "c": _CONFLICT_CHECK})).all()
            if not rows:
                break
            for pid, name, latin, kingdom in rows:
                inat = await _inat_by_ru(client, name, icache)
                await asyncio.sleep(0.5)
                sci = (inat or {}).get("sci")
                last_id = str(pid)
                if not sci or not _strong(name, (inat or {}).get("ru")):
                    nomatch += 1
                elif _genus_of(sci) == _genus_of(latin):
                    agree += 1
                else:
                    ginat = await _gbif_full(client, sci, gcache)
                    gstore = await _gbif_full(client, latin, gcache)
                    ik, sk = (ginat or {}).get("speciesKey"), (gstore or {}).get("speciesKey")
                    if ik and sk and ik == sk:
                        synonym += 1
                    else:
                        ok = bool(ginat and (ginat.get("match") or "").upper() in ("EXACT", "FUZZY")
                                  and _king_ok(kingdom, ginat.get("kingdom")))
                        proposed = (ginat or {}).get("canonical") or sci
                        ev = {"name": name, "stored": latin, "proposed": proposed, "gbif_ok": ok}
                        if ok:
                            async with async_session() as db:
                                await db.execute(text(
                                    "UPDATE plants SET name_latin=:l, inat_synced_at=NULL WHERE id=:i"),
                                    {"l": proposed, "i": pid})
                                await db.commit()
                            await _stage(pid, name, f"«{name}»: {latin} → {proposed}", "fix_latin", ev, _CONFLICT_CHECK)
                            async with async_session() as db:
                                await db.execute(text(
                                    "UPDATE data_quality_findings SET status='resolved' WHERE check_id=:c AND entity_id=:e"),
                                    {"c": _CONFLICT_CHECK, "e": str(pid)})
                                await db.commit()
                            fixed += 1
                        else:
                            await _stage(pid, name, f"«{name}»: {latin} ? {proposed}", "review", ev, _CONFLICT_CHECK)
                            review += 1
                activity.heartbeat({"last_id": last_id, "fixed": fixed, "review": review,
                                    "agree": agree, "nomatch": nomatch, "synonym": synonym})
    return {"phase": "conflict", "fixed": fixed, "review": review,
            "agree": agree, "nomatch": nomatch, "synonym": synonym}


# ------------------------------------------------------------------ recipe relink (2b)
@activity.defn
async def recipe_relink_activity() -> dict:
    """Corpus-wide 2b recipe→plant relink: clear every recipe/ingredient plant_id and
    rematch against the CLEANED plant identity (merged + latin-filled + conflict-fixed,
    alias.collision magnets already stripped in 1g — else the matcher re-captures the
    «вишня»→308-cherry mis-routing). Idempotent full recompute → retry-safe; heartbeats
    through the ~48K-row matching loop so it can't blow the timeout."""
    from app.services.plant_matching import relink_recipe_ingredients

    def _hb(done, total, linked):
        activity.heartbeat({"matched": done, "total": total, "linked": linked})

    async with async_session() as db:
        res = await relink_recipe_ingredients(db, progress=_hb)
    return {"phase": "recipe_relink", **res}


@activity.defn
async def genus_assembly_activity() -> dict:
    """Durable genus-tier assembly (RFC-reference-granularity): create a hub genus row
    (rank='genus') per Russian noun-token realized by ≥2 species, grounded by latin
    genus, and parent the confirmed members. Idempotent (find-or-create genus, first-
    claim parent_id) → a worker restart just resumes. Commits per genus; heartbeats
    through the ~2K-token loop. Run BEFORE the genus-aware relink."""
    from app.services.genus import build_genus_tier

    def _hb(done, total, genera):
        activity.heartbeat({"done": done, "total": total, "genera": genera})

    async with async_session() as db:
        res = await build_genus_tier(db, dry_run=False, progress=_hb)
    return {"phase": "genus_assembly", **res}
