"""Автоматический разбор очереди просмотра идентичности (``identity.reid_v2``, open).

Очередь копится из карточек, где модель и GBIF сошлись на виде или роде, но
русские имена не совпали по первому слову, и из карточек, где модель не
уверена. Смотреть их руками никто не будет, поэтому разбираем правилами, и
каждое правило опирается на проверяемое свидетельство:

- лексическая связь имён: общий корень слова, одинаковый видовой эпитет,
  одно и то же слово где-то в имени карточки, её старых названиях или в
  русском имени от модели, с поправкой на «ё», дореформенный «ъ» и одну
  опечатку в длинном слове;
- народные названия: имя карточки само оказывается народным названием
  предложенного таксона по GBIF (язык rus) или iNaturalist (locale=ru).
  Совпадение имени родовой карточки с названиями рода свидетельством не
  считается: оно ничего не говорит о самой карточке;
- названия частей и продуктов («кожура лимона», «овсяная солома») относят
  карточку к растению, которое назвала модель, если GBIF подтвердил вид
  точно и уверенность модели не ниже 80.

Что не доказано ни тем, ни другим, остаётся как есть и помечается
``dismissed`` с причиной. Род без вида это честное состояние карточки, его
не трогаем. Карточки, которые модель не смогла определить, проходят
отдельную классификацию: вещество уходит из гербария (``kingdom = вещество``),
часть растения сливается в карточку растения, если она находится однозначно.
Каждое слияние и переименование, как и раньше, пишется в ``card_identity_audit``.
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
from sqlalchemy import text

from app.database import async_session
from app.services.identity_cleanup import (
    CHECK_REID, GBIF_MATCH, GBIF_PACE, GBIF_SPECIES, _audit, _find_hub, _purge_qdrant, _relatin,
    binomial_core, merge_card, names_compatible,
)
from app.services.inaturalist import INAT_BASE
from app.services.llm import chat_completion_json
from app.services.plant_matching import _latin_key

logger = logging.getLogger(__name__)

Progress = Callable[[dict], None]

_INAT_HEADERS = {"User-Agent": "historical-recipes/1.0 (identity cleanup; contact via hist.begemot26.ru)"}
_PART_RE = re.compile(
    r"\b(корень|корни|корневищ\w*|трав[аыу]|лист\w*|цвет\w*|плод\w*|семен\w*|семя|семечк\w*|кор[аы]|ягод\w*|сок|"
    r"масл\w*|почк\w*|шишк\w*|клубн\w*|луковиц\w*|кожур\w*|цедр\w*|орех\w*|стебл\w*|побег\w*|соцвет\w*|солом\w*|"
    r"камед\w*|смол\w*|отруб\w*|мука|круп[аы]|сироп|отвар|настой)\b", re.I)
# Эпитеты, которые встречаются у сотен видов и потому ничего не доказывают.
_GENERIC_EPITHETS = {
    "лекарственн", "обыкновенн", "аптечн", "посевн", "полев", "лесн", "садов", "огородн", "культурн", "дик",
    "больш", "мал", "мелк", "крупн", "высок", "низк", "бел", "черн", "красн", "желт", "син", "зелен", "сер",
    "горьк", "сладк", "душист", "пахуч", "ядовит", "съедобн", "настоящ", "ложн", "европейск", "сибирск",
    "кавказск", "азиатск", "китайск", "японск", "американск", "восточн", "западн", "северн", "южн", "речн",
    "болотн", "горн", "лугов", "водян", "морск", "песчан", "степн", "приморск",
}
_SUFFIX_RE = re.compile(
    r"(ами|ями|ого|его|ому|ему|ыми|ими|ая|яя|ые|ие|ой|ей|ый|ий|ое|ее|ых|их|ам|ям|ах|ях|ом|ем|ов|ев|ы|и|а|я|у|ю|о|е|ь)$")

CLASSIFY_SYS = (
    "Ты фармакогност. Дано имя карточки из старого травника или поваренной книги и короткий контекст. "
    "Определи, что это: plant (растение или гриб как организм), part (часть или продукт растения: корень, "
    "трава, кожура, солома, масло, сок, камедь, крупа), substance (вещество или продукт не растительного "
    "происхождения либо химическое: щёлочь, прополис, мёд, вино, уабаин, сахар, соль), unknown. Для part "
    "назови растение по-русски в именительном падеже (одно слово, род или вид) в поле head. "
    "Строго JSON: {\"kind\":\"plant\"|\"part\"|\"substance\"|\"unknown\",\"head\":\"...\"|null}"
)


# ------------------------------------------------------------------ лексика

def _tokens(s: str | None) -> list[str]:
    s = (s or "").lower().replace("ё", "е").replace("ъ", "").replace("ѣ", "е").replace("і", "и")
    return [t for t in re.sub(r"[^а-я\s-]", " ", s).split() if len(t) >= 3]


def _stem(w: str) -> str:
    return _SUFFIX_RE.sub("", w)


def _tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    sa, sb = _stem(a), _stem(b)
    if len(sa) >= 4 and len(sb) >= 4 and (sa.startswith(sb) or sb.startswith(sa)):
        return True
    return len(a) >= 5 and len(b) >= 5 and Levenshtein.distance(a, b) <= 1


def lexical_link(card: str | None, ref: str | None, extra: list[str] | None = None) -> bool:
    """Связано ли имя карточки (плюс старые названия) с опорным именем: общий корень
    первого слова опорного имени с любым словом карточки, либо общий видовой эпитет."""
    tr = _tokens(ref)
    if not tr:
        return False
    for n in [card] + list(extra or []):
        tc = _tokens(n)
        if not tc:
            continue
        if any(_tok_eq(t, tr[0]) for t in tc):
            return True
        if len(tc) >= 2 and len(tr) >= 2 and len(_stem(tc[1])) >= 5 and _tok_eq(tc[1], tr[1]) \
                and _stem(tc[1]) not in _GENERIC_EPITHETS and _stem(tr[1]) not in _GENERIC_EPITHETS:
            return True
    return False


# ------------------------------------------------------------------ народные названия

class Vernacular:
    """Народные названия таксона по GBIF (rus) и iNaturalist (ru), с кэшем на прогон."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.cache: dict[str, set[str]] = {}

    async def names(self, latin: str | None) -> set[str]:
        q = re.sub(r"\s+[A-Z(].*$", "", (latin or "").replace("×", " ")).strip()
        if not q:
            return set()
        if q in self.cache:
            return self.cache[q]
        out: set[str] = set()
        try:
            d = (await self.client.get(GBIF_MATCH, params={"name": q})).json()
            key = d.get("usageKey")
            if key:
                v = (await self.client.get(f"{GBIF_SPECIES}/{int(key)}/vernacularNames", params={"limit": 200})).json()
                out |= {x["vernacularName"] for x in v.get("results", [])
                        if x.get("language") == "rus" and x.get("vernacularName")}
        except Exception:  # noqa: BLE001 — внешний справочник может молчать, это не ошибка прогона
            pass
        await asyncio.sleep(GBIF_PACE)
        try:
            t = (await self.client.get(f"{INAT_BASE}/taxa", params={"q": q, "locale": "ru", "per_page": 3},
                                       headers=_INAT_HEADERS)).json()
            for r in t.get("results", [])[:3]:
                if (r.get("name") or "").lower() == q.lower() and r.get("preferred_common_name"):
                    out.add(r["preferred_common_name"])
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.5)
        self.cache[q] = out
        return out

    async def confirms(self, name: str | None, latin: str | None, extra: list[str] | None = None) -> bool:
        vern = await self.names(latin)
        return any(lexical_link(name, v, extra) for v in vern)

    async def best_genus_name(self, genus: str | None) -> str | None:
        """Русское имя рода для родовой карточки: одно слово, не транслитерация латыни
        («слива», а не «прунус»), самое короткое из подходящих."""
        vern = await self.names(genus)
        words = []
        for v in vern:
            w = v.strip().lower().replace("ё", "е")
            if " " in w or len(w) < 4 or not re.fullmatch(r"[а-я-]+", w):
                continue
            if re.search(r"(ус|ум|ис|ия|иум|ея)$", w) and _stem(w) not in {"ирис"}:
                continue
            words.append(w)
        if not words:
            return None
        return sorted(words, key=lambda w: (len(w), w))[0].capitalize()


# ------------------------------------------------------------------ вспомогательное

async def _finding_close(pid, status: str, decision: dict) -> None:
    async with async_session() as db:
        await db.execute(text("""
            UPDATE data_quality_findings SET status = :st, last_seen = now(),
                   evidence = evidence || CAST(:ev AS jsonb)
            WHERE check_id = :cid AND entity_id = :eid"""),
            {"st": status, "ev": json.dumps({"resolve": decision}, ensure_ascii=False, default=str),
             "cid": CHECK_REID, "eid": str(pid)})
        await db.commit()


async def _find_head_card(db, head: str | None) -> dict | None:
    """Карточка растения по русскому имени: сначала родовая, потом единственная по имени."""
    if not head:
        return None
    rows = (await db.execute(text(
        "SELECT id, name, rank FROM plants WHERE lower(name) = :h AND kingdom IN ('растение','гриб') "
        "ORDER BY (rank = 'genus') DESC, (inat_taxon_id IS NOT NULL) DESC LIMIT 2"), {"h": head.strip().lower()})).all()
    if len(rows) == 1 or (len(rows) == 2 and rows[0].rank == "genus"):
        return {"id": rows[0].id, "name": rows[0].name}
    return None


async def _twin_for(db, pid, latin: str | None, twin_name: str | None):
    key = _latin_key(latin)
    if not key:
        return None
    rows = (await db.execute(text(
        "SELECT id, name, name_latin FROM plants WHERE id <> :id AND lower(name_latin) LIKE :g"),
        {"id": pid, "g": key.split()[0] + " %"})).all()
    twins = [t for t in rows if _latin_key(t.name_latin) == key]
    if not twins:
        return None
    return next((t for t in twins if t.name == twin_name), twins[0])


# ------------------------------------------------------------------ разбор

async def run_resolve(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    c: dict = {"seen": 0, "merge_hub": 0, "merge_twin": 0, "relatin": 0, "genus_kept": 0, "substance": 0,
               "part_merged": 0, "dismissed": 0, "errors": 0}
    samples: dict[str, list] = {}

    def note(rule: str, item: tuple) -> None:
        samples.setdefault(rule, [])
        if len(samples[rule]) < 6:
            samples[rule].append(item)

    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT f.entity_id, f.title, f.evidence, p.name, p.name_latin, p.names_historical
            FROM data_quality_findings f JOIN plants p ON p.id::text = f.entity_id
            WHERE f.check_id = :cid AND f.status = 'open'
            ORDER BY f.last_seen"""), {"cid": CHECK_REID})).all()
    if limit:
        rows = rows[:limit]

    dead: list[str] = []
    async with httpx.AsyncClient(timeout=25, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        vern = Vernacular(client)
        for eid, title, ev, name, latin, hist in rows:
            c["seen"] += 1
            pid = uuid.UUID(eid)
            ev = ev if isinstance(ev, dict) else json.loads(ev or "{}")
            russian = (ev.get("russian") or "").strip() or None
            proposed = (ev.get("proposed") or "").strip() or None
            conf = int(ev.get("conf") or 0)
            g = ev.get("gbif") or {}
            hist = list(hist or [])
            title = title or ""
            m_hub = re.search(r"родовая карточка «([^»]+)»", title)
            m_twin = re.search(r"двойник «([^»]+)»", title)
            decision: dict = {}
            status: str | None = None
            try:
                if m_hub:
                    # A2: род найден, родовая карточка названа иначе.
                    decision["class"] = "hub_mismatch"
                    genus = (latin or proposed or "").split()[0] if (latin or proposed) else None
                    async with async_session() as db:
                        hub = await _find_hub(db, genus, None) if genus else None
                    rule = None
                    hub_named_ok = bool(hub) and await vern.confirms(hub["name"], genus)
                    rename_to = None
                    if hub and not hub_named_ok:
                        # Родовая карточка названа не по-русски или чужим словом («Китайскии» у
                        # можжевельника): перед слиянием даём ей народное название рода.
                        rename_to = await vern.best_genus_name(genus)
                    ref_hub_name = rename_to or (hub["name"] if hub else None)
                    if hub and lexical_link(name, ref_hub_name, hist):
                        rule = "lexical:hub"
                    elif hub and hub_named_ok and russian and lexical_link(russian, hub["name"]):
                        rule = "model:hub"
                    elif hub and await vern.confirms(name, genus, hist):
                        rule = "vernacular:genus"
                    if hub and rule:
                        if apply:
                            async with async_session() as db:
                                if rename_to and rename_to.lower() != hub["name"].lower():
                                    await _audit(db, "resolve", "rename", hub["id"], hub["name"], genus,
                                                 extra={"new_name": rename_to, "reason": "hub named off vernacular"})
                                    await db.execute(text(
                                        "UPDATE plants SET names_historical = array_append(COALESCE(names_historical, ARRAY[]::text[]), name), "
                                        "name = :n WHERE id = :id"), {"n": rename_to, "id": hub["id"]})
                                    await db.execute(text(
                                        "UPDATE plant_reader_monograph SET monograph = monograph || jsonb_build_object('name', CAST(:n AS text)) "
                                        "WHERE plant_id = :id"), {"n": rename_to, "id": hub["id"]})
                                await merge_card(db, pid, hub["id"], "resolve", f"{rule}: {name} → hub {ref_hub_name}")
                                await db.commit()
                            dead.append(str(pid))
                        c["merge_hub"] += 1
                        status, decision["rule"], decision["target"] = "resolved", rule, ref_hub_name
                        if rename_to:
                            decision["hub_renamed_from"] = hub["name"]
                        note(rule, (name, "→", ref_hub_name, f"(hub was «{hub['name']}»)" if rename_to else ""))
                    else:
                        c["genus_kept"] += 1
                        status, decision["rule"] = "dismissed", "genus_kept:no_evidence"
                        note("genus_kept", (name, latin, hub["name"] if hub else None))

                elif m_twin:
                    # A: вид подтверждён и записан, двойник по латыни назван иначе.
                    decision["class"] = "twin_mismatch"
                    async with async_session() as db:
                        twin = await _twin_for(db, pid, latin, m_twin.group(1))
                    if twin is None:
                        c["dismissed"] += 1
                        status, decision["rule"] = "dismissed", "twin_gone"
                    else:
                        rule = None
                        card_ok = await vern.confirms(name, latin, hist)
                        twin_ok = await vern.confirms(twin.name, latin)
                        if lexical_link(name, twin.name, hist):
                            rule = "lexical:twin"
                        elif card_ok:
                            rule = "vernacular:species"
                        if rule:
                            if apply:
                                async with async_session() as db:
                                    await merge_card(db, pid, twin.id, "resolve", f"{rule}: {name} → twin {twin.name}")
                                    if card_ok and not twin_ok:
                                        # У двойника имя хуже (транслитерация, чужое слово): переносим на него имя карточки.
                                        await _audit(db, "resolve", "rename", twin.id, twin.name, latin,
                                                     extra={"new_name": name, "reason": "card name is vernacular, twin name is not"})
                                        await db.execute(text(
                                            "UPDATE plants SET names_historical = array_append(array_remove(COALESCE(names_historical, ARRAY[]::text[]), :n), name), "
                                            "name = :n WHERE id = :id"), {"n": name, "id": twin.id})
                                        await db.execute(text(
                                            "UPDATE plant_reader_monograph SET monograph = monograph || jsonb_build_object('name', CAST(:n AS text)) "
                                            "WHERE plant_id = :id"), {"n": name, "id": twin.id})
                                    await db.commit()
                                dead.append(str(pid))
                            c["merge_twin"] += 1
                            status, decision["rule"], decision["target"] = "resolved", rule, twin.name
                            note(rule, (name, "→", twin.name))
                        else:
                            c["dismissed"] += 1
                            status, decision["rule"] = "dismissed", ("twin_kept:twin_named_ok" if twin_ok else "twin_kept:no_evidence")
                            note("twin_kept", (name, "≠", twin.name))

                elif "родовой карточки нет" in title:
                    # B: род записан, родовой карточки нет. Честное состояние, оставляем.
                    decision["class"] = "genus_only"
                    c["genus_kept"] += 1
                    status, decision["rule"] = "dismissed", "genus_only:accepted"

                elif "не определила" in title:
                    # D: модель не определила. Классифицируем: вещество, часть, растение.
                    decision["class"] = "unknown"
                    try:
                        cls = await chat_completion_json(
                            [{"role": "system", "content": CLASSIFY_SYS},
                             {"role": "user", "content": json.dumps(
                                 {"name": name, "latin": latin, "old_names": hist[:5]}, ensure_ascii=False)}],
                            task="plant_extraction", temperature=0.0, max_tokens=120)
                    except Exception:  # noqa: BLE001 — временный отказ модели, карточка останется open
                        c["errors"] += 1
                        continue
                    kind = (cls.get("kind") if isinstance(cls, dict) else None) or "unknown"
                    head = (cls.get("head") if isinstance(cls, dict) else None) or None
                    if kind == "substance":
                        if apply:
                            async with async_session() as db:
                                await _audit(db, "resolve", "kingdom", pid, name, latin, extra={"kind": "substance"})
                                await db.execute(text("UPDATE plants SET kingdom = 'вещество' WHERE id = :id"), {"id": pid})
                                await db.commit()
                        c["substance"] += 1
                        status, decision["rule"] = "resolved", "classified:substance"
                        note("substance", (name,))
                    elif kind == "part" and head and head.strip().lower() != (name or "").strip().lower() \
                            and not _PART_RE.search(head):
                        async with async_session() as db:
                            target = await _find_head_card(db, head)
                        if target and names_compatible(head, target["name"]) and target["name"].lower() != (name or "").lower():
                            if apply:
                                async with async_session() as db:
                                    await merge_card(db, pid, target["id"], "resolve", f"part → {target['name']} (head {head})")
                                    await db.commit()
                                dead.append(str(pid))
                            c["part_merged"] += 1
                            status, decision["rule"], decision["target"] = "resolved", "classified:part", target["name"]
                            note("part", (name, "→", target["name"]))
                        else:
                            c["dismissed"] += 1
                            status, decision["rule"] = "dismissed", f"part:no_unique_head({head})"
                            note("part_nohead", (name, head))
                    else:
                        c["dismissed"] += 1
                        status, decision["rule"] = "dismissed", f"classified:{kind}"
                        note("unknown_kept", (name, latin))

                else:
                    # C: вид предложен, автоматика его не применила.
                    decision["class"] = "species_review"
                    is_bino = bool(proposed and len(proposed.split()) >= 2 and proposed.upper() != "UNKNOWN")
                    canonical = g.get("name") or proposed
                    ok_gbif = g.get("matchType") in ("EXACT", "FUZZY") and g.get("rank") == "SPECIES"
                    exact_strong = g.get("matchType") == "EXACT" and conf >= 80
                    rule = None
                    if is_bino and ok_gbif and canonical:
                        if _PART_RE.search(name or "") and russian and exact_strong:
                            rule = "part_phrase"
                        elif russian and exact_strong and lexical_link(name, russian, hist):
                            rule = "lexical:model"
                        elif await vern.confirms(name, canonical, hist):
                            rule = "vernacular:species"
                    if rule:
                        new_latin = binomial_core(canonical) or canonical
                        merged = None
                        if apply:
                            async with async_session() as db:
                                await _relatin(db, pid, name, latin or "", new_latin, "resolve", "relatin",
                                               {"rule": rule, **ev})
                                if russian and await vern.confirms(russian, new_latin):
                                    await db.execute(text(
                                        "UPDATE plants SET name_modern = COALESCE(name_modern, :r) WHERE id = :id"),
                                        {"r": russian, "id": pid})
                                twin = await _twin_for(db, pid, new_latin, None)
                                if twin is not None:
                                    ref_name = russian if rule == "part_phrase" else name
                                    if names_compatible(ref_name, twin.name) or lexical_link(ref_name, twin.name, hist):
                                        await merge_card(db, pid, twin.id, "resolve", f"{rule} → twin {twin.name}")
                                        merged = {"merged_into": str(twin.id), "target": twin.name}
                                await db.commit()
                            if merged:
                                dead.append(str(pid))
                        c["relatin"] += 1
                        status, decision["rule"], decision["latin"] = "resolved", rule, new_latin
                        if merged:
                            decision["target"] = merged["target"]
                        note(rule, (name, "→", new_latin, russian))
                    else:
                        c["dismissed"] += 1
                        status, decision["rule"] = "dismissed", "species:no_evidence"
                        note("species_kept", (name, proposed, russian, conf, g.get("matchType")))
            except Exception as e:  # noqa: BLE001 — одна карточка не должна ронять разбор
                logger.warning("resolve failed for %s: %s", name, e)
                c["errors"] += 1
                continue
            if apply and status:
                await _finding_close(pid, status, decision)
            if progress:
                progress({"step": "resolve", **c})
            if len(dead) >= 200:
                await _purge_qdrant(dead)
                dead = []
    if dead:
        await _purge_qdrant(dead)
    return {"step": "resolve", "apply": apply, **c, "samples": samples}
