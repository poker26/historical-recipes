"""Чистка идентичности карточек: дубли, оболочки, латынь через GBIF, переопределение.

Что чистим. Две группы карточек, которые добор фото не берёт, потому что у них
сломана сама идентичность (замер 25.09.2026, см. RFC-botanik-site §9):

- группа B, «латынь есть, iNaturalist не узнал»: 4 878 карточек, из них 1 959
  без единого факта;
- группа C, «латыни нет совсем»: 4 250 видовых карточек, из них 1 886 без фактов,
  а 1 193 это статьи лечебника XV века «Ненужное для неучей» с именами в
  армянской, арабской и персидской транслитерации. Их откладываем целиком:
  они станут историческими именами настоящих видов отдельной работой.

Четыре шага, в этом порядке:

1. ``dedup``: карточка без узнанной латыни, у которой есть ровно одна тёзка с
   латынью и таксоном или проверенным очерком, сливается в тёзку.
2. ``shells``: оболочка без фактов (нет применений, состава, рецептов,
   токсичности, сбора, ареала, свойств, масел, определений и дочерних карточек)
   сливается в родовую карточку, если та есть, иначе удаляется.
3. ``gbif``: бином группы B проверяется в GBIF; точное совпадение или синоним
   переписывают латынь на принятое имя, старое имя уходит в исторические;
   нечёткое совпадение принимается только при точном роде и не больше двух
   правок в эпитете. Отметка похода в iNaturalist сбрасывается, и фото-проход
   подхватывает карточку сам.
4. ``reid``: оставшиеся карточки с фактами переопределяет модель по полному
   контексту записи (имя, латынь, семейство, описание, состав, действия,
   книги), GBIF подтверждает; вид пишется только при уверенности, иначе род и
   слияние в родовую карточку, сомнительное уходит в очередь на просмотр.

Каждое слияние и удаление пишется целиком в ``card_identity_audit`` до
действия, чтобы карточку можно было восстановить. Все шаги идемпотентны:
сделанное помечается в базе (слитая или удалённая строка исчезает, у
переопределённой появляется отметка в ``data_quality_findings``), и повторный
запуск продолжает с остатка.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Callable

import httpx
from rapidfuzz.distance import Levenshtein
from sqlalchemy import text, update

from app.database import async_session
from app.models.ingredient import Ingredient
from app.models.plant import PlantCompatibility
from app.models.recipe import RecipeIngredient
from app.services import qdrant
from app.services.llm import chat_completion_json
from app.services.plant_matching import _PLANT_CHILD_MODELS, _latin_key

logger = logging.getLogger(__name__)

GBIF_MATCH = "https://api.gbif.org/v1/species/match"
GBIF_SPECIES = "https://api.gbif.org/v1/species"
GBIF_PACE = 0.4
KINGDOM_HINT = {"растение": "Plantae", "гриб": "Fungi"}
AMIRDOVLAT = "Ненужное для неучей"
CHECK_GBIF = "identity.gbif_fix"
CHECK_REID = "identity.reid_v2"
MAX_TRANSIENT_STREAK = 10
BATCH = 200

_BINOMIAL = re.compile(r"^([A-Z][a-z]+)\s+(?:×\s*)?([a-z][a-z-]{2,})")
_CYR = re.compile(r"[А-Яа-яЁё]")

Progress = Callable[[dict], None]

# ------------------------------------------------------------------ условия выборки

GROUP_B = ("p.kingdom IN ('растение','гриб') AND p.inat_synced_at IS NOT NULL "
           "AND p.inat_taxon_id IS NULL AND p.photo_url IS NULL AND p.rank <> 'genus'")
GROUP_C = "p.kingdom IN ('растение','гриб') AND p.rank = 'species' AND p.name_latin IS NULL"
NO_FACTS = """
    NOT EXISTS (SELECT 1 FROM plant_medicinal_uses x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_culinary_uses x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_compounds x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_toxicities x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_harvests x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_habitats x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plant_properties x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM recipe_ingredients x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM essential_oils x WHERE x.plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM identifications x WHERE x.matched_plant_id = p.id)
    AND NOT EXISTS (SELECT 1 FROM plants c WHERE c.parent_id = p.id)
"""
# Карточка, известная только по «Ненужному для неучей»: откладываем целиком.
AMIRDOVLAT_ONLY = """
    (EXISTS (SELECT 1 FROM plant_book_mentions m JOIN books b ON b.id = m.book_id
             WHERE m.plant_id = p.id AND b.title = :amir)
     AND NOT EXISTS (SELECT 1 FROM plant_book_mentions m JOIN books b ON b.id = m.book_id
                     WHERE m.plant_id = p.id AND b.title <> :amir))
"""
FACTS_SCORE = """
    ((SELECT count(*) FROM plant_medicinal_uses x WHERE x.plant_id = p.id)
   + (SELECT count(*) FROM plant_compounds x WHERE x.plant_id = p.id)
   + (SELECT count(*) FROM plant_culinary_uses x WHERE x.plant_id = p.id)
   + (SELECT count(*) FROM recipe_ingredients x WHERE x.plant_id = p.id))
"""


def binomial_core(latin: str | None) -> str | None:
    m = _BINOMIAL.match((latin or "").strip())
    return f"{m.group(1)} {m.group(2)}" if m else None


def ru_head(name: str | None) -> str | None:
    """Первое слово русского имени в нижнем регистре: «Ромашка лекарственная» → «ромашка».
    Для латинского или битого имени ``None``."""
    if not name or not _CYR.search(name):
        return None
    tok = re.split(r"[\s,;()]+", name.strip().lower())
    return tok[0] if tok and tok[0] else None


def names_compatible(source_name: str | None, target_name: str | None) -> bool:
    """Слияние допустимо, когда русские имена начинаются с одного слова, либо одно из
    имён не русское (латынь или битое). «Лук» и «Лук-чеснок» несовместимы, и это
    правильно: сливать лук в чеснок нельзя даже при совпавшей латыни."""
    a, b = ru_head(source_name), ru_head(target_name)
    if a is None or b is None:
        return True
    return a == b


# ------------------------------------------------------------------ журнал, слияние, удаление

async def _plant_snapshot(db, pid) -> dict:
    row = (await db.execute(text("SELECT row_to_json(p)::text FROM plants p WHERE id = :id"), {"id": pid})).scalar()
    counts = (await db.execute(text("""
        SELECT (SELECT count(*) FROM plant_book_mentions x WHERE x.plant_id = :id) AS mentions,
               (SELECT count(*) FROM plant_medicinal_uses x WHERE x.plant_id = :id) AS uses,
               (SELECT count(*) FROM plant_compounds x WHERE x.plant_id = :id) AS compounds,
               (SELECT count(*) FROM recipe_ingredients x WHERE x.plant_id = :id) AS recipes"""), {"id": pid})).first()
    return {"plant": json.loads(row) if row else None,
            "counts": dict(counts._mapping) if counts else {}}


async def _audit(db, step: str, action: str, pid, name, latin, target=None, extra: dict | None = None) -> None:
    snap = await _plant_snapshot(db, pid)
    if extra:
        snap.update(extra)
    await db.execute(text("""
        INSERT INTO card_identity_audit (step, action, plant_id, name, name_latin, target_id, target_name, payload)
        VALUES (:step, :action, :pid, :name, :latin, :tid, :tname, CAST(:payload AS jsonb))"""),
        {"step": step, "action": action, "pid": pid, "name": name, "latin": latin,
         "tid": (target or {}).get("id"), "tname": (target or {}).get("name"),
         "payload": json.dumps(snap, ensure_ascii=False, default=str)})


async def merge_card(db, source_id, target_id, step: str, reason: str) -> None:
    """Сливает карточку ``source`` в ``target``: все факты, ссылки из рецептов,
    определения, масла, биотопы переезжают; имя источника становится
    историческим названием цели; источник удаляется. Журнал пишется первым."""
    src = (await db.execute(text(
        "SELECT name, name_latin, names_historical, parts_used, family, family_latin, description, is_toxic, kingdom, "
        "qdrant_collection FROM plants WHERE id = :id"), {"id": source_id})).first()
    tgt = (await db.execute(text(
        "SELECT name, names_historical, parts_used, family, family_latin, description, is_toxic, kingdom "
        "FROM plants WHERE id = :id"), {"id": target_id})).first()
    if src is None or tgt is None:
        return
    await _audit(db, step, "merge", source_id, src.name, src.name_latin,
                 target={"id": target_id, "name": tgt.name}, extra={"reason": reason})

    for model in _PLANT_CHILD_MODELS:
        await db.execute(update(model).where(model.plant_id == source_id).values(plant_id=target_id))
    await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_a_id == source_id).values(plant_a_id=target_id))
    await db.execute(update(PlantCompatibility).where(PlantCompatibility.plant_b_id == source_id).values(plant_b_id=target_id))
    await db.execute(text("DELETE FROM plant_compatibility WHERE plant_a_id = plant_b_id"))
    await db.execute(update(RecipeIngredient).where(RecipeIngredient.plant_id == source_id).values(plant_id=target_id))
    await db.execute(update(Ingredient).where(Ingredient.plant_id == source_id).values(plant_id=target_id))
    p = {"s": source_id, "t": target_id}
    await db.execute(text("UPDATE identifications SET matched_plant_id = :t WHERE matched_plant_id = :s"), p)
    await db.execute(text("UPDATE essential_oils SET plant_id = :t WHERE plant_id = :s"), p)
    await db.execute(text("UPDATE plants SET parent_id = :t WHERE parent_id = :s"), p)
    await db.execute(text(
        "UPDATE plant_biotopes SET plant_id = :t WHERE plant_id = :s AND NOT EXISTS "
        "(SELECT 1 FROM plant_biotopes b2 WHERE b2.plant_id = :t AND b2.biotope = plant_biotopes.biotope)"), p)
    await db.execute(text("DELETE FROM plant_biotopes WHERE plant_id = :s"), p)
    await db.execute(text("DELETE FROM plant_pairings WHERE plant_a = :s OR plant_b = :s"), p)

    hist = list(tgt.names_historical or [])
    for h in (src.names_historical or []):
        if h and h not in hist:
            hist.append(h)
    if src.name and src.name != tgt.name and src.name not in hist:
        hist.append(src.name)
    parts = list(tgt.parts_used or [])
    for pt in (src.parts_used or []):
        if pt and pt not in parts:
            parts.append(pt)
    await db.execute(text("""
        UPDATE plants SET names_historical = CAST(:hist AS text[]), parts_used = CAST(:parts AS text[]),
               family = COALESCE(family, :family), family_latin = COALESCE(family_latin, :family_latin),
               description = COALESCE(description, :description),
               is_toxic = (is_toxic OR :toxic),
               kingdom = CASE WHEN kingdom = 'растение' AND :kingdom = 'гриб' THEN 'гриб' ELSE kingdom END
        WHERE id = :t"""),
        {"hist": hist or None, "parts": parts or None, "family": src.family, "family_latin": src.family_latin,
         "description": src.description, "toxic": bool(src.is_toxic), "kingdom": src.kingdom, "t": target_id})
    await db.execute(text("DELETE FROM plants WHERE id = :s"), p)


async def delete_card(db, pid, step: str, reason: str) -> None:
    row = (await db.execute(text("SELECT name, name_latin FROM plants WHERE id = :id"), {"id": pid})).first()
    if row is None:
        return
    await _audit(db, step, "delete", pid, row.name, row.name_latin, extra={"reason": reason})
    await db.execute(text("DELETE FROM plants WHERE id = :id"), {"id": pid})


async def _purge_qdrant(ids: list[str]) -> None:
    for i in range(0, len(ids), 256):
        try:
            await qdrant.delete_points("plants_v2", ids[i:i + 256])
        except Exception as e:  # noqa: BLE001 — сиротская точка не должна ронять прогон
            logger.warning("qdrant purge failed: %s", e)


async def _finding(pid, check: str, status: str, title: str, evidence: dict, action: str) -> None:
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen)
            VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:fix AS jsonb), false, :st, now(), now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              title = EXCLUDED.title, evidence = EXCLUDED.evidence, suggested_fix = EXCLUDED.suggested_fix,
              status = EXCLUDED.status, last_seen = now()"""),
            {"id": str(uuid.uuid4()), "cid": check, "eid": str(pid), "title": title[:200], "st": status,
             "ev": json.dumps(evidence, ensure_ascii=False, default=str),
             "fix": json.dumps({"action": action, "plant_id": str(pid)}, ensure_ascii=False)})
        await db.commit()


async def _find_hub(db, latin_genus: str | None, first_word: str | None) -> dict | None:
    """Родовая карточка с русским именем: по латинскому роду, иначе по первому слову имени."""
    if latin_genus:
        r = (await db.execute(text(
            "SELECT id, name FROM plants WHERE rank = 'genus' AND name !~ '[A-Za-z]' "
            "AND lower(split_part(name_latin, ' ', 1)) = lower(:g) ORDER BY name LIMIT 1"), {"g": latin_genus})).first()
        if r:
            return {"id": r.id, "name": r.name}
    if first_word and len(first_word) >= 3:
        r = (await db.execute(text(
            "SELECT id, name FROM plants WHERE rank = 'genus' AND lower(name) = lower(:w) LIMIT 1"), {"w": first_word})).first()
        if r:
            return {"id": r.id, "name": r.name}
    return None


async def _merge_into_same_latin(db, pid, latin: str, step: str) -> dict | None:
    """Если после починки латыни у карточки появился двойник с тем же биномом,
    сливает более бедную в более богатую. Возвращает описание слияния."""
    key = _latin_key(latin)
    if not key:
        return None
    genus = key.split()[0]
    others = (await db.execute(text(f"""
        SELECT p.id, p.name, p.name_latin, {FACTS_SCORE} AS score, (p.inat_taxon_id IS NOT NULL) AS has_taxon
        FROM plants p WHERE p.id <> :id AND p.name_latin IS NOT NULL AND lower(p.name_latin) LIKE :g"""),
        {"id": pid, "g": genus + " %"})).all()
    twins = [o for o in others if _latin_key(o.name_latin) == key]
    if not twins:
        return None
    me = (await db.execute(text(f"SELECT p.id, p.name, p.name_latin, {FACTS_SCORE} AS score, (p.inat_taxon_id IS NOT NULL) AS has_taxon "
                                f"FROM plants p WHERE p.id = :id"), {"id": pid})).first()
    twin = max(twins, key=lambda o: (o.has_taxon, o.score))
    if not names_compatible(me.name, twin.name):
        # Одна латынь, разные русские имена: не сливаем автоматически, оставляем на просмотр.
        return {"skipped_incompatible": str(twin.id), "twin_name": twin.name}
    if (twin.has_taxon, twin.score) >= (me.has_taxon, me.score):
        await merge_card(db, me.id, twin.id, step, "same latin key after relatin")
        return {"merged_into": str(twin.id), "target_name": twin.name}
    await merge_card(db, twin.id, me.id, step, "same latin key after relatin (twin poorer)")
    return {"absorbed": str(twin.id), "absorbed_name": twin.name}


# ------------------------------------------------------------------ шаг 1: дубли по имени

async def run_dedup(apply: bool, progress: Progress | None = None) -> dict:
    async with async_session() as db:
        rows = (await db.execute(text(f"""
            SELECT p.id, p.name, p.name_latin, p.kingdom,
                   (SELECT json_agg(json_build_object('id', q.id, 'name', q.name, 'latin', q.name_latin)
                           ORDER BY (q.inat_taxon_id IS NOT NULL) DESC, (q.photo_url IS NOT NULL) DESC)
                    FROM plants q
                    WHERE q.id <> p.id AND lower(q.name) = lower(p.name) AND q.name_latin IS NOT NULL
                      AND q.kingdom = p.kingdom
                      AND (q.inat_taxon_id IS NOT NULL
                           OR EXISTS (SELECT 1 FROM plant_reader_monograph m WHERE m.plant_id = q.id AND m.reviewed)))
                   AS twins
            FROM plants p
            WHERE (({GROUP_B}) OR ({GROUP_C})) AND length(p.name) >= 3 AND NOT {AMIRDOVLAT_ONLY}"""),
            {"amir": AMIRDOVLAT})).all()
    plan = []
    skipped_ambiguous = 0
    for pid, name, latin, kingdom, twins in rows:
        twins = twins or []
        if isinstance(twins, str):
            twins = json.loads(twins)
        if not twins:
            continue
        if len(twins) > 1:
            skipped_ambiguous += 1
            continue
        t = twins[0]
        core = binomial_core(latin)
        if core and t.get("latin") and binomial_core(t["latin"]) \
                and core.split()[0].lower() != binomial_core(t["latin"]).split()[0].lower():
            skipped_ambiguous += 1      # имена совпали, а латинские роды разные: не трогаем
            continue
        plan.append({"id": str(pid), "name": name, "latin": latin, "target": t})
    result = {"step": "dedup", "apply": apply, "candidates": len(plan),
              "skipped_ambiguous": skipped_ambiguous, "sample": plan[:15]}
    if not apply:
        return result
    merged = 0
    dead: list[str] = []
    for item in plan:
        async with async_session() as db:
            await merge_card(db, uuid.UUID(item["id"]), uuid.UUID(item["target"]["id"]), "dedup", "same name, single twin with latin")
            await db.commit()
        dead.append(item["id"])
        merged += 1
        if progress:
            progress({"step": "dedup", "merged": merged, "of": len(plan)})
        if len(dead) >= 200:
            await _purge_qdrant(dead)
            dead = []
    if dead:
        await _purge_qdrant(dead)
    result["merged"] = merged
    return result


# ------------------------------------------------------------------ шаг 2: оболочки

async def run_shells(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    counters = {"merged": 0, "deleted": 0, "seen": 0}
    samples: list[dict] = []
    dead: list[str] = []
    while True:
        async with async_session() as db:
            rows = (await db.execute(text(f"""
                SELECT p.id, p.name, p.name_latin, p.rank FROM plants p
                WHERE (({GROUP_B}) OR ({GROUP_C})) AND {NO_FACTS} AND NOT {AMIRDOVLAT_ONLY}
                ORDER BY p.name LIMIT :n"""), {"amir": AMIRDOVLAT, "n": BATCH})).all()
        if not rows:
            break
        if not apply:
            # Сухой прогон: считаем и показываем, куда бы что ушло.
            async with async_session() as db:
                total = (await db.execute(text(f"""
                    SELECT count(*) FROM plants p
                    WHERE (({GROUP_B}) OR ({GROUP_C})) AND {NO_FACTS} AND NOT {AMIRDOVLAT_ONLY}"""),
                    {"amir": AMIRDOVLAT})).scalar()
                to_merge = 0
                for pid, name, latin, rank in rows[:120]:
                    core = binomial_core(latin)
                    hub = await _find_hub(db, core.split()[0] if core else None,
                                          (name or "").split()[0] if _CYR.search(name or "") else None)
                    if hub:
                        to_merge += 1
                    if len(samples) < 20:
                        samples.append({"name": name, "latin": latin, "hub": (hub or {}).get("name")})
            return {"step": "shells", "apply": False, "total": total,
                    "merge_share_in_sample": f"{to_merge}/{min(len(rows), 120)}", "sample": samples}
        for pid, name, latin, rank in rows:
            if limit and counters["seen"] >= limit:
                return {"step": "shells", "apply": True, **counters, "stopped": "limit"}
            core = binomial_core(latin)
            async with async_session() as db:
                hub = await _find_hub(db, core.split()[0] if core else None,
                                      (name or "").split()[0] if _CYR.search(name or "") else None)
                if hub:
                    await merge_card(db, pid, hub["id"], "shells", "shell without facts -> genus hub")
                    counters["merged"] += 1
                else:
                    await delete_card(db, pid, "shells", "shell without facts, no genus hub")
                    counters["deleted"] += 1
                await db.commit()
            dead.append(str(pid))
            counters["seen"] += 1
            if progress:
                progress({"step": "shells", **counters})
            if len(dead) >= 200:
                await _purge_qdrant(dead)
                dead = []
    if dead:
        await _purge_qdrant(dead)
    return {"step": "shells", "apply": True, **counters, "stopped": "done"}


# ------------------------------------------------------------------ шаг 3: латынь через GBIF

async def gbif_match(client: httpx.AsyncClient, name: str, kingdom: str | None) -> dict | None:
    """Совпадение GBIF. ``None`` означает временный отказ, dict с ``matchType``
    ``NONE`` означает, что GBIF имени не знает."""
    params = {"name": name}
    if kingdom:
        params["kingdom"] = kingdom
    for attempt in range(3):
        try:
            r = await client.get(GBIF_MATCH, params=params)
            if r.status_code == 200:
                return r.json()
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def gbif_accepted(client: httpx.AsyncClient, usage_key: int) -> str | None:
    for attempt in range(3):
        try:
            r = await client.get(f"{GBIF_SPECIES}/{int(usage_key)}")
            if r.status_code == 200:
                d = r.json()
                return d.get("canonicalName") or d.get("scientificName")
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(1.5 * (attempt + 1))
    return None


def _fuzzy_ok(old_core: str, new_core: str) -> bool:
    og, oe = old_core.lower().split()[:2]
    ng, ne = new_core.lower().split()[:2]
    return og == ng and Levenshtein.distance(oe, ne) <= 2


async def _relatin(db, pid, name, old_latin: str, new_latin: str, step: str, action: str, evidence: dict) -> None:
    await _audit(db, step, action, pid, name, old_latin, extra={"new_latin": new_latin, **evidence})
    await db.execute(text("""
        UPDATE plants SET
            names_historical = CASE WHEN :old = '' OR :old = ANY(COALESCE(names_historical, ARRAY[]::text[])) THEN names_historical
                                    ELSE array_append(COALESCE(names_historical, ARRAY[]::text[]), :old) END,
            name_latin = :new,
            name = CASE WHEN name !~ '[А-Яа-яЁё]' THEN :new ELSE name END,
            inat_synced_at = NULL
        WHERE id = :id"""), {"old": old_latin, "new": new_latin, "id": pid})


async def run_gbif(limit: int = 0, progress: Progress | None = None) -> dict:
    c = {"seen": 0, "retry": 0, "synonym": 0, "fuzzy": 0, "review": 0, "unknown": 0, "transient": 0, "merged": 0}
    streak = 0
    seen: set = set()
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (identity cleanup)"}) as client:
        while not (limit and c["seen"] >= limit):
            async with async_session() as db:
                rows = (await db.execute(text(f"""
                    SELECT p.id, p.name, p.name_latin, p.kingdom, {FACTS_SCORE} AS score FROM plants p
                    WHERE {GROUP_B} AND p.name_latin ~ '^[A-Z][a-z]+ [a-z]'
                      AND NOT EXISTS (SELECT 1 FROM data_quality_findings f WHERE f.check_id = :chk AND f.entity_id = p.id::text)
                    ORDER BY {FACTS_SCORE} DESC, p.name LIMIT :n"""),
                    {"chk": CHECK_GBIF, "n": BATCH})).all()
            rows = [r for r in rows if r.id not in seen]
            if not rows:
                break
            for pid, name, latin, kingdom, score in rows:
                if limit and c["seen"] >= limit:
                    break
                seen.add(pid)
                core = binomial_core(latin)
                if not core:
                    await _finding(pid, CHECK_GBIF, "dismissed", f"{name}: латынь не бином", {"latin": latin}, "reid")
                    c["seen"] += 1
                    continue
                d = await gbif_match(client, core, KINGDOM_HINT.get(kingdom or "растение"))
                await asyncio.sleep(GBIF_PACE)
                if d is None:
                    c["transient"] += 1
                    streak += 1
                    if streak >= MAX_TRANSIENT_STREAK:
                        return {"step": "gbif", **c, "stopped": "transient_streak"}
                    continue
                streak = 0
                c["seen"] += 1
                mt, rank, status = d.get("matchType"), d.get("rank"), d.get("status")
                ev = {"latin": latin, "core": core, "gbif": {"matchType": mt, "rank": rank, "status": status,
                      "name": d.get("canonicalName") or d.get("scientificName"), "confidence": d.get("confidence")}}
                if mt not in ("EXACT", "FUZZY") or rank not in ("SPECIES", "SUBSPECIES", "VARIETY"):
                    c["unknown"] += 1
                    await _finding(pid, CHECK_GBIF, "dismissed", f"{name}: GBIF не знает вида", ev, "reid")
                    if progress:
                        progress({"step": "gbif", **c})
                    continue
                accepted = d.get("canonicalName") or d.get("scientificName") or core
                if status == "SYNONYM" and d.get("acceptedUsageKey"):
                    acc = await gbif_accepted(client, d["acceptedUsageKey"])
                    await asyncio.sleep(GBIF_PACE)
                    if acc:
                        accepted = acc
                accepted_core = binomial_core(accepted) or accepted
                ev["accepted"] = accepted_core
                if mt == "FUZZY" and not _fuzzy_ok(core, binomial_core(accepted_core) or accepted_core):
                    c["review"] += 1
                    await _finding(pid, CHECK_GBIF, "open", f"{name}: {core} → {accepted_core}? (нечётко)", ev, "review")
                    if progress:
                        progress({"step": "gbif", **c})
                    continue
                async with async_session() as db:
                    if accepted_core.lower() == core.lower():
                        c["retry"] += 1
                        await _audit(db, "gbif", "retry", pid, name, latin, extra={"gbif": ev["gbif"]})
                        await db.execute(text("UPDATE plants SET inat_synced_at = NULL WHERE id = :id"), {"id": pid})
                        merged = None
                    else:
                        c["fuzzy" if mt == "FUZZY" else "synonym"] += 1
                        await _relatin(db, pid, name, latin, accepted_core, "gbif", "relatin", ev)
                        merged = await _merge_into_same_latin(db, pid, accepted_core, "gbif")
                        if merged:
                            c["merged"] += 1
                    await db.commit()
                if merged and merged.get("merged_into"):
                    await _purge_qdrant([str(pid)])
                elif merged and merged.get("absorbed"):
                    await _purge_qdrant([merged["absorbed"]])
                if merged and merged.get("skipped_incompatible"):
                    c["review"] += 1
                    await _finding(pid, CHECK_GBIF, "open",
                                   f"{name}: {core} → {accepted_core}, двойник «{merged['twin_name']}» с другим именем",
                                   {**ev, "merge": merged}, "review_merge")
                else:
                    await _finding(pid, CHECK_GBIF, "resolved", f"{name}: {core} → {accepted_core}", {**ev, "merge": merged}, "done")
                if progress:
                    progress({"step": "gbif", **c})
    return {"step": "gbif", **c, "stopped": "limit" if (limit and c["seen"] >= limit) else "done"}


# ------------------------------------------------------------------ шаг 4: переопределение моделью

_SYS = (
    "Ты ботаник-систематик. Перед тобой карточка растения или гриба из старой книги "
    "(травник, флора, словарь названий). Имя карточки может быть испорчено распознаванием, "
    "может быть латынью с ошибками, русским народным, областным, устаревшим или дореформенным "
    "названием. Семейство, описание, химический состав, действия и книги-источники обычно верны. "
    "Восстанови ПРИНЯТЫЙ научный бином (род вид), который имелся в виду. "
    "Битая латынь часто кодирует его (кириллица вместо латиницы: $→S, Г,.→L., и→u, Б→b; "
    "первая буква тоже могла исказиться). ЖЁСТКИЕ ПРАВИЛА: "
    "(1) автор в латыни («DC.», «Bunge», «C. Chr.») сильно ограничивает род, учитывай его; "
    "(2) предложенный род обязан согласоваться с семейством и местообитанием из описания; "
    "если противоречит, это не он; "
    "(3) химия и действия это подсказка, не доказательство; "
    "(4) НЕ УГАДЫВАЙ вид: если уверенно читается только род, верни один род и confidence ≤ 60; "
    "UNKNOWN, если и род ненадёжен. "
    "Строго JSON: {\"latin\":\"Genus species\"|\"Genus\"|\"UNKNOWN\","
    "\"russian\":\"современное русское название\"|null,\"confidence\":0-100,\"reason\":\"кратко\"}."
)


def _kingdom_ok(card_kingdom: str | None, gbif_kingdom: str | None) -> bool:
    if not gbif_kingdom:
        return False
    if (card_kingdom or "").startswith("гриб"):
        return gbif_kingdom == "Fungi"
    return gbif_kingdom in ("Plantae", "Chromista")


async def _context(db, pid) -> dict:
    r = (await db.execute(text("""
        SELECT p.name, p.name_latin, p.family, p.family_latin, p.kingdom, p.description, p.parts_used, p.names_historical,
          (SELECT string_agg(DISTINCT c.name, ', ') FROM plant_compounds pc JOIN compounds c ON c.id = pc.compound_id WHERE pc.plant_id = p.id) AS compounds,
          (SELECT string_agg(DISTINCT COALESCE(u.canon_action, u.action_raw), ', ') FROM plant_medicinal_uses u WHERE u.plant_id = p.id) AS actions,
          (SELECT string_agg(DISTINCT u.indications, '; ') FROM (SELECT indications FROM plant_medicinal_uses u WHERE u.plant_id = p.id AND indications IS NOT NULL LIMIT 6) u) AS indications,
          (SELECT string_agg(DISTINCT cu.use, '; ') FROM (SELECT use FROM plant_culinary_uses cu WHERE cu.plant_id = p.id AND use IS NOT NULL LIMIT 4) cu) AS culinary,
          (SELECT string_agg(DISTINCT h.biotope, '; ') FROM (SELECT biotope FROM plant_habitats h WHERE h.plant_id = p.id AND biotope IS NOT NULL LIMIT 3) h) AS habitat,
          (SELECT string_agg(DISTINCT b.title || COALESCE(' (' || b.year::text || ')', ''), '; ')
             FROM (SELECT DISTINCT bk.title, bk.year FROM plant_book_mentions m JOIN books bk ON bk.id = m.book_id WHERE m.plant_id = p.id LIMIT 4) b) AS books,
          (SELECT left(m.original_text, 400) FROM plant_book_mentions m WHERE m.plant_id = p.id AND m.original_text IS NOT NULL ORDER BY length(m.original_text) DESC LIMIT 1) AS mention
        FROM plants p WHERE p.id = :id"""), {"id": pid})).first()
    if r is None:
        return {}
    return {
        "name": r.name, "name_latin": r.name_latin, "family": r.family or r.family_latin, "kingdom": r.kingdom,
        "description": (r.description or "")[:400], "parts": r.parts_used, "other_names": (r.names_historical or [])[:5],
        "compounds": (r.compounds or "")[:250], "actions": (r.actions or "")[:200], "indications": (r.indications or "")[:200],
        "culinary": (r.culinary or "")[:150], "habitat": (r.habitat or "")[:150], "books": (r.books or "")[:250],
        "source_excerpt": r.mention or "",
    }


async def _db_has_genus(db, genus: str) -> bool:
    n = (await db.execute(text(
        "SELECT count(*) FROM plants WHERE name_latin ILIKE :g AND name_latin ~ '^[A-Z][a-z]+ [a-z]+'"),
        {"g": genus + " %"})).scalar()
    return (n or 0) >= 1


async def run_reid(limit: int = 0, progress: Progress | None = None) -> dict:
    c = {"seen": 0, "species": 0, "genus_hub": 0, "genus_only": 0, "review": 0, "errors": 0, "merged": 0}
    streak = 0
    seen: set = set()
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (identity cleanup)"}) as client:
        while not (limit and c["seen"] >= limit):
            async with async_session() as db:
                rows = (await db.execute(text(f"""
                    SELECT p.id, p.name, p.name_latin, p.kingdom FROM plants p
                    WHERE (({GROUP_B}) OR ({GROUP_C})) AND NOT ({NO_FACTS}) AND NOT {AMIRDOVLAT_ONLY}
                      AND NOT EXISTS (SELECT 1 FROM data_quality_findings f WHERE f.check_id = :chk AND f.entity_id = p.id::text)
                      AND NOT EXISTS (SELECT 1 FROM data_quality_findings f WHERE f.check_id = :gchk AND f.entity_id = p.id::text AND f.status = 'resolved')
                    ORDER BY {FACTS_SCORE} DESC, p.name LIMIT :n"""),
                    {"amir": AMIRDOVLAT, "chk": CHECK_REID, "gchk": CHECK_GBIF, "n": BATCH})).all()
            rows = [r for r in rows if r.id not in seen]
            if not rows:
                break
            for pid, name, latin, kingdom in rows:
                if limit and c["seen"] >= limit:
                    break
                seen.add(pid)
                async with async_session() as db:
                    ctx = await _context(db, pid)
                try:
                    llm = await chat_completion_json(
                        [{"role": "system", "content": _SYS},
                         {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
                        task="plant_extraction", temperature=0.1, max_tokens=600)
                except Exception as e:  # noqa: BLE001 — временный отказ модели, карточка вернётся в следующий прогон
                    c["errors"] += 1
                    streak += 1
                    if streak >= MAX_TRANSIENT_STREAK:
                        return {"step": "reid", **c, "stopped": "llm_errors"}
                    await asyncio.sleep(3)
                    continue
                streak = 0
                c["seen"] += 1
                if not isinstance(llm, dict):
                    llm = {}
                sci = (llm.get("latin") or "").strip()
                russian = (llm.get("russian") or "").strip() or None
                try:
                    conf = int(llm.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0
                ev = {"name": name, "latin": latin, "proposed": sci, "russian": russian, "conf": conf,
                      "reason": llm.get("reason")}
                if not sci or sci.upper() == "UNKNOWN":
                    c["review"] += 1
                    await _finding(pid, CHECK_REID, "open", f"{name}: модель не определила", ev, "review")
                    if progress:
                        progress({"step": "reid", **c})
                    continue
                is_binomial = len(sci.split()) >= 2
                genus = sci.split()[0]
                g = await gbif_match(client, sci, KINGDOM_HINT.get(kingdom or "растение"))
                await asyncio.sleep(GBIF_PACE)
                gmt = (g or {}).get("matchType")
                gconf = (g or {}).get("confidence") or 0
                grank = (g or {}).get("rank")
                canonical = (g or {}).get("canonicalName") or (g or {}).get("scientificName")
                kok = _kingdom_ok(kingdom, (g or {}).get("kingdom"))
                ev["gbif"] = {"matchType": gmt, "rank": grank, "name": canonical, "confidence": gconf}
                decided = None
                # Русское имя карточки и русское имя от модели должны начинаться с одного
                # слова: «Лук» → «Чеснок» это другое растение, как бы уверенно модель ни звучала.
                ru_ok = names_compatible(name, russian) if russian else True
                if is_binomial and gmt == "EXACT" and grank == "SPECIES" and gconf >= 85 and kok and conf >= 80 \
                        and canonical and _fuzzy_ok(sci, canonical) and ru_ok:
                    new_latin = binomial_core(canonical) or canonical
                    async with async_session() as db:
                        await _relatin(db, pid, name, latin or "", new_latin, "reid", "relatin", ev)
                        if russian:
                            await db.execute(text("UPDATE plants SET name_modern = COALESCE(name_modern, :r) WHERE id = :id"),
                                             {"r": russian, "id": pid})
                        merged = await _merge_into_same_latin(db, pid, new_latin, "reid")
                        await db.commit()
                    if merged and (merged.get("merged_into") or merged.get("absorbed")):
                        c["merged"] += 1
                        await _purge_qdrant([str(pid)] if merged.get("merged_into") else [merged.get("absorbed")])
                    c["species"] += 1
                    if merged and merged.get("skipped_incompatible"):
                        decided = ("open", f"{name} → {new_latin}, двойник «{merged['twin_name']}» с другим именем", "review_merge")
                    else:
                        decided = ("resolved", f"{name} → {new_latin}", "done")
                elif is_binomial and gmt in ("EXACT", "FUZZY") and grank == "SPECIES" and kok and conf >= 50:
                    # Вид назван, но уверенности для автоматики не хватает: на просмотр, ничего не меняем.
                    c["review"] += 1
                    decided = ("open", f"{name}: {sci}? (conf {conf}, GBIF {gmt}{'' if ru_ok else ', имя расходится'})", "review")
                else:
                    gg = g if not is_binomial else await gbif_match(client, genus, KINGDOM_HINT.get(kingdom or "растение"))
                    if is_binomial:
                        await asyncio.sleep(GBIF_PACE)
                    genus_ok = bool(gg and gg.get("matchType") in ("EXACT", "FUZZY") and _kingdom_ok(kingdom, gg.get("kingdom"))
                                    and (gg.get("canonicalName") or "").split()[:1] == [genus])
                    async with async_session() as db:
                        if not genus_ok and conf >= 50:
                            genus_ok = await _db_has_genus(db, genus)
                        if genus_ok:
                            hub = await _find_hub(db, genus, None)
                            if hub and names_compatible(name, hub["name"]):
                                await merge_card(db, pid, hub["id"], "reid", f"genus-level re-id → hub ({sci}, conf {conf})")
                                await db.commit()
                                await _purge_qdrant([str(pid)])
                                c["genus_hub"] += 1
                                decided = ("resolved", f"{name} → род {genus} (слито в «{hub['name']}»)", "done")
                            elif hub:
                                # Род совпал, а русские имена расходятся («Смородина» и «Крыжовник»):
                                # латынь рода пишем, слияние оставляем человеку.
                                await _relatin(db, pid, name, latin or "", genus, "reid", "relatin_genus", ev)
                                await db.commit()
                                c["genus_only"] += 1
                                decided = ("open", f"{name} → род {genus}, родовая карточка «{hub['name']}» названа иначе", "review_merge")
                            else:
                                await _relatin(db, pid, name, latin or "", genus, "reid", "relatin_genus", ev)
                                await db.commit()
                                c["genus_only"] += 1
                                decided = ("open", f"{name} → род {genus}, родовой карточки нет", "review")
                        else:
                            c["review"] += 1
                            decided = ("open", f"{name}: {sci}? (conf {conf}, GBIF {gmt})", "review")
                status, title, action = decided
                # Отметка ставится и на слитую карточку: её id остаётся в журнале и в findings.
                await _finding(pid, CHECK_REID, status, title, ev, action)
                if progress:
                    progress({"step": "reid", **c})
    return {"step": "reid", **c, "stopped": "limit" if (limit and c["seen"] >= limit) else "done"}
