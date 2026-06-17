"""Fill name_latin for cards that have a CLEAN Russian name but NULL latin (40% of
the herbarium → invisible to the latin-keyed dedup). Same proven machinery as the
re-ID pass — qwen3-235b full-context (russian name + family + description + chemistry)
→ GBIF verify (retry) with a local-DB genus fallback — but the input is a clean name
(not OCR garbage), so the russian name is the strong signal. Sets ONLY name_latin
(KEEPS the Russian name) + unsync for photo. Species → full latin, genus → genus
level, else → review. Idempotent.

Usage:  python fill_latin.py pilot         — the NULL-latin Aconitum cards (known answers)
        python fill_latin.py [N]           — full run (optional LIMIT)
"""
import asyncio
import json
import re
import sys

import httpx
from sqlalchemy import text

from app.database import async_session
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
from app.temporal.cleanup_activities import _king_ok

CHECK = "identity.fill_latin"
_SYS = (
    "Ты ботаник-систематик. Дано ЧИСТОЕ русское название растения (+ опц. семейство/"
    "описание/химия). Русское название обычно содержит И РОД, И ВИДОВОЙ ЭПИТЕТ — "
    "переведи его в ПРИНЯТЫЙ научный БИНОМ: «Аконит джунгарский»→Aconitum soongaricum, "
    "«Аконит байкальский»→Aconitum baicalense, «Борец Кузнецова»→Aconitum kusnezoffii. "
    "Семейство/описание/химия — для снятия ОМОНИМИИ (народное имя может совпасть у "
    "разных видов). ВЕРНИ ВИД, когда эпитет определим (это перевод, не угадывание); "
    "только если видовой эпитет реально неоднозначен/неизвестен — верни один РОД "
    "(confidence ≤ 60). UNKNOWN, если и род ненадёжен. Строго JSON: "
    "{\"latin\":\"Genus species\"|\"Genus\"|\"UNKNOWN\",\"confidence\":0-100,\"reason\":\"кратко\"}."
)


async def _gbif_retry(client, sci, tries=3):
    for k in range(tries):
        g = await _resolve_one(client, sci)
        if g and g.get("match_type"):
            return g
        await asyncio.sleep(1.2 * (k + 1))
    return None


async def _db_has_genus(genus):
    async with async_session() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM plants WHERE name_latin ILIKE :g AND name_latin ~ '^[A-Z][a-z]+ [a-z]+'"),
            {"g": genus + " %"})).scalar()
    return (n or 0) >= 1


async def decide(client, name, family, kingdom, desc, comps, acts):
    payload = json.dumps({"name": name, "family": family, "description": (desc or "")[:300],
                          "compounds": (comps or "")[:200], "actions": (acts or "")[:200]}, ensure_ascii=False)
    try:
        llm = await chat_completion_json(
            [{"role": "system", "content": _SYS}, {"role": "user", "content": payload}],
            task="plant_extraction", temperature=0.1, max_tokens=500)
    except Exception as e:
        return {"action": "error", "reason": str(e)[:80]}
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
        out["action"], out["latin"], out["via"] = "apply_genus", genus, "local_db"
    else:
        out["action"] = "review"
    return out


async def _fix(pid, latin):
    async with async_session() as s:
        await s.execute(text(
            "UPDATE plants SET name_latin=:l, inat_synced_at=NULL WHERE id=CAST(:i AS uuid)"),
            {"l": latin, "i": pid})
        await s.commit()


async def _record(pid, status, ev):
    async with async_session() as s:
        await s.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (gen_random_uuid(), :c, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:ev AS jsonb), false, :st, now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              evidence=EXCLUDED.evidence, status=EXCLUDED.status, last_seen=now()
        """), {"c": CHECK, "eid": pid, "title": (ev.get("name") or "")[:120],
               "ev": json.dumps(ev, ensure_ascii=False), "st": status})
        await s.commit()


async def main():
    pilot = len(sys.argv) > 1 and sys.argv[1] == "pilot"
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    base = (r"name_latin IS NULL AND name ~ '[А-Яа-яЁё]' AND name !~ '[A-Za-z0-9]' "
            r"AND array_length(regexp_split_to_array(btrim(name),'\s+'),1) >= 2")
    async with async_session() as s:
        q = (f"SELECT p.id, p.name, p.family, p.kingdom, p.description, "
             f"(SELECT string_agg(DISTINCT c.name, ', ') FROM plant_compounds pc JOIN compounds c ON c.id=pc.compound_id WHERE pc.plant_id=p.id), "
             f"(SELECT string_agg(DISTINCT u.action_raw, ', ') FROM plant_medicinal_uses u WHERE u.plant_id=p.id AND u.action_raw IS NOT NULL) "
             f"FROM plants p WHERE {base} ")
        if pilot:
            q += "AND (name ILIKE 'борец%' OR name ILIKE 'аконит%')"
        else:
            q += f"AND p.id::text NOT IN (SELECT entity_id FROM data_quality_findings WHERE check_id='{CHECK}' AND status='resolved')"
            if n:
                q += f" ORDER BY random() LIMIT {n}"
        rows = (await s.execute(text(q))).all()
    print(f"targets: {len(rows)} (pilot={pilot})", flush=True)
    sp = ge = rv = er = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i, (pid, name, family, kingdom, desc, comps, acts) in enumerate(rows):
            r = await decide(client, name, family, kingdom, desc, comps, acts)
            act = r.get("action")
            if pilot:
                print(f"  {name!r} → llm={r.get('proposed')!r} gbif={r.get('gbif')!r} → {r.get('latin')!r} [{act}] {('via='+r['via']) if r.get('via') else ''}")
                continue
            if act in ("apply_species", "apply_genus"):
                await _fix(str(pid), r["latin"]); await _record(str(pid), "resolved", r)
                sp += act == "apply_species"; ge += act == "apply_genus"
            elif act == "error":
                er += 1
            else:
                await _record(str(pid), "open", r); rv += 1
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(rows)}: species={sp} genus={ge} review={rv} err={er}", flush=True)
    if not pilot:
        print(f"DONE species={sp} genus={ge} review={rv} err={er}", flush=True)


asyncio.run(main())
