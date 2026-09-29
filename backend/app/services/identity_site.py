"""Ошибки карточек, которые видит поисковик (сайт botanik.fun), замер 29.09.2026.

Открытые находки по карточкам индекса наполовину устарели: карточки с тех пор исправлены
прогонами reid и resolve, а находки остались. Поэтому ошибки перемерены по текущим данным
на 6 106 карточках индекса: 259 групп, где у нескольких карточек один вид при разных
именах; 143 карточки с видовым именем и латынью только рода; около 40 имён в
дореформенной орфографии или испорченных распознаванием; больше 800 устаревших находок.

Шаги (``scripts/identity_run.py --step <шаг> [--apply] [--limit N]``, сухой прогон без
--apply только считает и показывает выборку):

- ``stale``: закрыть находки, чья проблема в карточке уже исправлена или самой карточки
  больше нет. Меняется только статус находки.
- ``oldspell``: имя в дореформенной орфографии («Мох исландскій», «Ленъ») переписывается
  в современной. Правка принимается, только если каждое изменённое слово встречается в
  именах других карточек в современном написании («Маръ» становится «Марь», потому что
  «марь» известна, а «мар» нет; «Бересклѣдъ» остаётся, потому что сейчас пишут
  «бересклет»). Старое имя уходит в исторические названия.
- ``genuslatin``: у карточки видовое имя («Цикута ядовитая»), а латынь только рода
  («Cicuta»). Вид ищется в iNaturalist по русскому имени; принимается, только если
  русское имя iNaturalist совпадает с именем карточки по первому и последнему слову,
  род тот же, что у карточки, а GBIF знает бином как вид растения или гриба того же
  царства. Прописной эпитет («Astragalus Sieversianus») исправляется строчным при
  точном совпадении в GBIF. Всё остальное пишется находкой ``identity.site_latin``.
- ``sametaxon``: у нескольких карточек один вид. Сливаются только карточки, имя каждой
  из которых подтверждено народными названиями вида (GBIF rus, iNaturalist ru), и
  карточки с испорченным именем («Етойит асшапит (Г.) ГНеёг.» при Erodium cicutarium).
  Карточка, чьё имя не подтверждено («Голубика» с латынью черники), не трогается и
  получает находку ``identity.site_mismatch``: у неё, скорее всего, неверна латынь.
- ``junkname``: испорченное имя без двойника заменяется русским именем вида из
  iNaturalist или GBIF.

Каждое слияние и переименование пишется в ``card_identity_audit`` (step ``site-*``).
Слияние сбрасывает уровень съедобности цели: после ``sametaxon --apply`` запустить
``scripts/edible_safety_run.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import defaultdict
from typing import Callable

import httpx
from sqlalchemy import literal_column, select, text

from app.database import async_session
from app.models.plant import Plant
from app.services.identity_cleanup import (
    FACTS_SCORE, GBIF_PACE, GBIF_SPECIES, _audit, _kingdom_ok, _purge_qdrant, gbif_accepted, gbif_match,
    merge_card,
)
from app.services.identity_resolve import Vernacular, _INAT_HEADERS
from app.services.inaturalist import INAT_BASE

logger = logging.getLogger(__name__)
Progress = Callable[[dict], None]

CHECK_MISMATCH = "identity.site_mismatch"
CHECK_LATIN = "identity.site_latin"

_CYR = re.compile(r"[А-Яа-яЁё]")
_OLD = re.compile(r"[ѣіѳѵѢІѲѴ]|[ъЪ](?=$|[^а-яёА-ЯЁ])")
_OLD_MAP = str.maketrans({"ѣ": "е", "Ѣ": "Е", "і": "и", "І": "И", "ѳ": "ф", "Ѳ": "Ф", "ѵ": "и", "Ѵ": "И"})
# Испорченное распознаванием имя: заглавная внутри слова («ГНеёг»), инициал автора в
# скобках или в конце («(Г.)», «Втазяса Г.»). Это латынь, прочитанная как кириллица.
_JUNK = re.compile(r"[а-яё][А-ЯЁ]|\([А-ЯЁA-Z]{1,3}\.\)|\s[А-ЯЁ][а-яё]?\.\s*$")
_EPI = re.compile(r"^([A-Z][a-z]+)(?:\s+(?:×\s*)?([A-Za-z][a-z-]{2,}))?")
_NOT_EPI = {"sp", "spp", "var", "subsp", "ssp", "ex", "et", "and", "und", "l"}


def is_junk(name: str | None) -> bool:
    return bool(_JUNK.search(name or ""))


def latin_core(latin: str | None) -> tuple[str | None, str | None, bool]:
    """(род, эпитет, эпитет_с_прописной). Автор, «sp.», «L.» эпитетом не считаются.
    Слово с прописной считается эпитетом старой записи («Aconitum Anthora L.») только без
    точки после него (иначе это сокращение автора: «Adenophora Fisch.») и если за ним не
    идёт ещё одно строчное слово (составной эпитет «Filix femina» так не разобрать)."""
    raw = (latin or "").strip()
    m = _EPI.match(raw)
    if not m:
        return None, None, False
    genus, epi = m.group(1), m.group(2)
    if epi and epi.lower() in _NOT_EPI:
        epi = None
    capital = bool(epi and epi[0].isupper())
    if capital:
        rest = raw[m.end():]
        if len(epi) < 4 or rest.startswith(".") or re.match(r"\s+[a-z]{3,}", rest):
            epi, capital = None, False
    return genus, epi.lower() if epi else None, capital


def _ru_words(name: str | None) -> list[str]:
    head = re.split(r",|\(|\sили\s|;", name or "")[0]
    return [w for w in re.findall(r"[а-яё-]+", head.lower().replace("ё", "е")) if len(w) >= 2]


def _stem(w: str) -> str:
    return w[:max(3, min(6, len(w) - 2))]


def ru_strong_match(card_name: str | None, other: str | None) -> bool:
    """Первое и последнее слово совпадают по основе: «Цикута ядовитая» = «цикута ядовитая»."""
    a, b = _ru_words(card_name), _ru_words(other)
    if len(a) < 2 or len(b) < 2:
        return False
    return _stem(a[0]) == _stem(b[0]) and _stem(a[-1]) == _stem(b[-1])


_PART_WORD = re.compile(r"^(плод|корн|корен|лист|цвет|трав|семен|семя|кора|коры|ягод|почк|шишк|клубн|луковиц|"
                        r"сок|масл|побег|стебл|кожур)")


def species_evidence(name: str | None, vernaculars: set[str]) -> str | None:
    """Подтверждено ли имя карточки народными названиями вида. «name»: имя совпадает с
    народным названием по первому и последнему слову (одно слово сравнивается с первым
    словом названия); «part»: это часть вида («Плоды черники»). Совпадение только первого
    слова у двусловного имени не считается: «Береза маньчжурская» и «берёза повислая»
    это разные виды одного рода. Исторические названия карточки не используются: среди
    них бывают чужие."""
    a = _ru_words(name)
    if not a:
        return None
    for v in vernaculars:
        b = _ru_words(v)
        if not b:
            continue
        if len(a) >= 2 and len(b) >= 2 and ru_strong_match(name, v):
            return "name"
        if len(a) == 1 and len(b) == 1 and _stem(a[0]) == _stem(b[0]):
            return "name"
        if len(a) == 2 and _PART_WORD.match(a[0]) and _stem(a[1]) == _stem(b[0]):
            return "part"
    # Одно слово имени совпало только с первым словом народного названия («Прострел» и
    # «прострел обыкновенный»): это род, а не вид. Не повод ни сливать, ни помечать.
    if len(a) == 1 and any(_ru_words(v) and _stem(a[0]) == _stem(_ru_words(v)[0]) for v in vernaculars):
        return "weak"
    return None


async def _published(db, *cols):
    score = literal_column(FACTS_SCORE.replace("p.id", "plants.id")).label("score")
    kids = literal_column("(SELECT count(*) FROM plants c WHERE c.parent_id = plants.id)").label("kids")
    from app.routers.plants import PUBLISHED_PRED
    return (await db.execute(select(
        Plant.id, Plant.name, Plant.name_latin, Plant.kingdom, Plant.rank, Plant.names_historical,
        score, kids, *cols).where(PUBLISHED_PRED))).all()


async def _finding(db, check_id: str, pid, title: str, evidence: dict) -> None:
    await db.execute(text("""
        INSERT INTO data_quality_findings
          (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
           auto_fixable, status, first_seen, last_seen)
        VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                CAST(:fix AS jsonb), false, 'open', now(), now())
        ON CONFLICT (check_id, entity_id) DO UPDATE SET
          title = EXCLUDED.title, evidence = EXCLUDED.evidence, status = 'open', last_seen = now()"""),
        {"id": str(uuid.uuid4()), "cid": check_id, "eid": str(pid), "title": title[:300],
         "ev": json.dumps(evidence, ensure_ascii=False, default=str),
         "fix": json.dumps({"action": "review", "plant_id": str(pid)})})


# ------------------------------------------------------------------ stale

async def run_stale(apply: bool, progress: Progress | None = None) -> dict:
    """Закрыть находки, чья проблема уже исправлена или карточки больше нет."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT f.id, f.check_id, f.title, f.evidence, p.id AS pid, p.name, p.name_latin
            FROM data_quality_findings f LEFT JOIN plants p ON p.id::text = f.entity_id::text
            WHERE f.status = 'open' AND f.entity_type = 'plant' AND f.check_id = ANY(:checks)"""),
            {"checks": ["norm.mixed_script", "identity.latin_backfill", "identity.fill_latin",
                        "identity.latin_ocr_garbled", "identity.name_ocr_garbled",
                        "identity.latin_unresolvable"]})).all()
        close: dict[str, list] = defaultdict(list)
        keep: dict[str, int] = defaultdict(int)
        for f in rows:
            ev = f.evidence or {}
            why = None
            if f.pid is None:
                why = "карточки больше нет (слита или удалена)"
            elif f.check_id == "norm.mixed_script" and not _CYR.search(f.name_latin or ""):
                why = "в латыни карточки больше нет кириллицы"
            elif f.check_id in ("identity.latin_backfill", "identity.fill_latin") and latin_core(f.name_latin)[0] \
                    and "не растение" not in (f.title or ""):
                why = "латынь у карточки уже заполнена"
            elif f.check_id == "identity.latin_ocr_garbled" and ev.get("garbled_latin") != f.name_latin:
                why = "битая латынь в карточке уже заменена"
            elif f.check_id == "identity.name_ocr_garbled" and ev.get("name") and ev.get("name") != f.name:
                why = "битое имя в карточке уже заменено"
            elif f.check_id == "identity.latin_unresolvable" and ev.get("name_latin") != f.name_latin:
                why = "латынь в карточке уже другая"
            if why:
                close[why].append(f.id)
            else:
                keep[f.check_id] += 1
        result = {"step": "stale", "apply": apply, "close": {k: len(v) for k, v in close.items()},
                  "closed": sum(len(v) for v in close.values()), "keep_open": dict(keep)}
        if apply:
            for why, ids in close.items():
                await db.execute(text("""
                    UPDATE data_quality_findings SET status = 'stale', resolved_by = 'identity-site',
                           resolved_at = now(), note = :why WHERE id = ANY(:ids)"""), {"why": why, "ids": ids})
            await db.commit()
    return result


# ------------------------------------------------------------------ oldspell

async def _lexicon(db) -> set[str]:
    names = (await db.execute(text(
        "SELECT name FROM plants WHERE kingdom IN ('растение', 'гриб') AND name IS NOT NULL"))).scalars().all()
    words: set[str] = set()
    for n in names:
        if _OLD.search(n):
            continue
        words |= {w.lower().replace("ё", "е") for w in re.findall(r"[А-Яа-яЁё-]+", n)}
    return words


def modernize(name: str, lexicon: set[str]) -> str | None:
    """Имя в современной орфографии или None, если хоть одно слово не удалось проверить."""
    parts = re.split(r"([^А-Яа-яЁёѢѣІіѲѳѴѵ-]+)", name)
    out = []
    for p in parts:
        if not _OLD.search(p):
            out.append(p)
            continue
        base = p.translate(_OLD_MAP)
        cands = [base]
        if base.endswith(("ъ", "Ъ")):
            cands = [base[:-1], base[:-1] + "ь"]
        good = [c for c in cands if c.lower().replace("ё", "е") in lexicon]
        if not good:
            return None
        out.append(good[0])
    new = "".join(out)
    return new if new != name and not _OLD.search(new) else None


async def run_oldspell(apply: bool, progress: Progress | None = None) -> dict:
    async with async_session() as db:
        lex = await _lexicon(db)
        rows = (await db.execute(text("""
            SELECT id, name, name_latin FROM plants
            WHERE kingdom IN ('растение', 'гриб') AND name ~ '[ѣіѳѵѢІѲѴ]|[ъЪ]([^а-яёА-ЯЁ]|$)'"""))).all()
    plan, skipped = [], []
    for r in rows:
        new = modernize(r.name, lex)
        (plan if new else skipped).append({"id": str(r.id), "old": r.name, "new": new, "latin": r.name_latin})
    result = {"step": "oldspell", "apply": apply, "candidates": len(rows), "rename": len(plan),
              "skipped": len(skipped), "sample": plan[:40], "skipped_sample": skipped[:25]}
    if apply:
        async with async_session() as db:
            for p in plan:
                await _audit(db, "site-oldspell", "rename", uuid.UUID(p["id"]), p["old"], p["latin"],
                             extra={"new_name": p["new"]})
                await db.execute(text("""
                    UPDATE plants SET name = :new,
                        names_historical = CASE WHEN :old = ANY(COALESCE(names_historical, ARRAY[]::text[]))
                                                THEN names_historical
                                                ELSE array_append(COALESCE(names_historical, ARRAY[]::text[]), :old) END
                    WHERE id = CAST(:id AS uuid) AND name = :old"""), {"new": p["new"], "old": p["old"], "id": p["id"]})
            await db.commit()
    return result


# ------------------------------------------------------------------ genuslatin

async def _inat_species_by_ru(client: httpx.AsyncClient, ru: str) -> list[dict]:
    try:
        r = await client.get(f"{INAT_BASE}/taxa", params={"q": ru, "locale": "ru", "per_page": 8, "rank": "species"},
                             headers=_INAT_HEADERS)
        return r.json().get("results", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        await asyncio.sleep(1.2)


async def _gbif_by_vernacular(client: httpx.AsyncClient, ru: str) -> list[tuple[str, str]]:
    """Виды основного справочника GBIF, у которых есть русское народное название,
    похожее на запрос: [(бином, русское название)]."""
    out: list[tuple[str, str]] = []
    try:
        r = await client.get(f"{GBIF_SPECIES}/search", params={
            "q": ru, "qField": "VERNACULAR", "rank": "SPECIES", "limit": 10,
            "datasetKey": "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"})
        for t in (r.json().get("results", []) if r.status_code == 200 else []):
            sci = t.get("canonicalName")
            for v in t.get("vernacularNames") or []:
                if v.get("language") == "rus" and sci and v.get("vernacularName"):
                    out.append((sci, v["vernacularName"]))
    except (httpx.HTTPError, ValueError):
        pass
    await asyncio.sleep(GBIF_PACE)
    return out


async def _gbif_species(client: httpx.AsyncClient, binomial: str, card_kingdom: str | None,
                        exact_only: bool = False) -> str | None:
    """Принятый бином по GBIF, если это вид нужного царства и совпадение точное (или, без
    exact_only, нечёткое с высокой уверенностью); иначе None."""
    kingdom = "Fungi" if (card_kingdom or "").startswith("гриб") else "Plantae"
    d = await gbif_match(client, binomial, kingdom)
    await asyncio.sleep(GBIF_PACE)
    ok_types = ("EXACT",) if exact_only else ("EXACT", "FUZZY")
    if not d or d.get("matchType") not in ok_types or (d.get("confidence") or 0) < 90:
        return None
    if d.get("rank") != "SPECIES" or not _kingdom_ok(card_kingdom, d.get("kingdom")):
        return None
    if d.get("status") == "ACCEPTED":
        return d.get("canonicalName")
    key = d.get("acceptedUsageKey") or d.get("usageKey")
    acc = await gbif_accepted(client, key) if key else None
    await asyncio.sleep(GBIF_PACE)
    return acc


async def run_genuslatin(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    async with async_session() as db:
        rows = await _published(db)
    cands = []
    for r in rows:
        genus, epi, capital = latin_core(r.name_latin)
        if not genus or r.rank == "genus" or is_junk(r.name):
            continue
        if (epi is None or capital) and len(_ru_words(r.name)) >= 2:
            cands.append((r, genus, epi, capital))
    if limit:
        cands = cands[:limit]
    c = {"step": "genuslatin", "apply": apply, "candidates": len(cands), "relatin": 0, "review": 0,
         "sample": [], "review_sample": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        for n, (r, genus, epi, capital) in enumerate(cands, 1):
            new, why, inat_hint = None, None, None
            if capital and epi:
                new = await _gbif_species(client, f"{genus} {epi}", r.kingdom, exact_only=True)
                why = "эпитет с прописной, GBIF знает строчный" if new else None
            if not new:
                ru = " ".join(_ru_words(r.name)[:3])
                for t in await _inat_species_by_ru(client, ru):
                    tname, tru = t.get("name") or "", t.get("preferred_common_name") or ""
                    if not tru or not ru_strong_match(r.name, tru):
                        continue
                    inat_hint = f"{tname} ({tru})"
                    if tname.split()[0].lower() != genus.lower():
                        why = f"iNaturalist даёт другой род: {inat_hint}"
                        break
                    new = await _gbif_species(client, tname, r.kingdom)
                    why = f"iNaturalist: {inat_hint}" if new else f"GBIF не подтвердил {tname}"
                    break
            if not new and not (why or "").startswith("iNaturalist даёт другой род"):
                for sci, vru in await _gbif_by_vernacular(client, ru):
                    if not ru_strong_match(r.name, vru) or sci.split()[0].lower() != genus.lower():
                        continue
                    new = await _gbif_species(client, sci, r.kingdom)
                    if new:
                        why = f"GBIF, народное название: {sci} ({vru})"
                        break
            if new and new.split()[0].lower() == genus.lower() or (new and capital):
                c["relatin"] += 1
                if len(c["sample"]) < 60:
                    c["sample"].append({"name": r.name, "old": r.name_latin, "new": new, "why": why})
                if apply:
                    async with async_session() as db:
                        await _audit(db, "site-genuslatin", "relatin", r.id, r.name, r.name_latin,
                                     extra={"new_latin": new, "why": why})
                        await db.execute(text("UPDATE plants SET name_latin = :new, inat_synced_at = NULL "
                                              "WHERE id = :id AND name_latin IS NOT DISTINCT FROM :old"),
                                         {"new": new, "id": r.id, "old": r.name_latin})
                        await db.commit()
            else:
                c["review"] += 1
                if len(c["review_sample"]) < 40:
                    c["review_sample"].append({"name": r.name, "latin": r.name_latin, "why": why or "iNaturalist не нашёл вид с таким именем", "new": new})
                if apply:
                    async with async_session() as db:
                        await _finding(db, CHECK_LATIN, r.id,
                                       f"«{r.name}»: видовое имя, а латынь только рода ({r.name_latin})",
                                       {"name": r.name, "latin": r.name_latin, "why": why, "proposed": new,
                                        "inat": inat_hint})
                        await db.commit()
            if progress and n % 10 == 0:
                progress({k: v for k, v in c.items() if not isinstance(v, list)} | {"done": n})
    return c


# ------------------------------------------------------------------ sametaxon

async def run_sametaxon(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    async with async_session() as db:
        rows = await _published(db)
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        genus, epi, _cap = latin_core(r.name_latin)
        if genus and epi:
            groups[(genus.lower(), epi, r.kingdom)].append(r)
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    keys = sorted(groups)[: limit or None]
    c = {"step": "sametaxon", "apply": apply, "groups": len(keys), "merged": 0, "flagged": 0,
         "groups_merged": 0, "no_vernacular": 0, "sample": [], "flag_sample": []}
    dead: list[str] = []
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        vern = Vernacular(client)
        for n, key in enumerate(keys, 1):
            members = groups[key]
            binomial = f"{key[0].capitalize()} {key[1]}"
            names = await vern.names(binomial)
            confirmed, parts, junk, unconfirmed = [], [], [], []
            for m in members:
                ev = None if is_junk(m.name) else species_evidence(m.name, names)
                if is_junk(m.name):
                    junk.append(m)
                elif ev == "name":
                    confirmed.append(m)
                elif ev == "part":
                    parts.append(m)
                elif ev == "weak":
                    continue
                else:
                    unconfirmed.append(m)
            if not names:
                c["no_vernacular"] += 1
            pool = confirmed or ([] if not junk else sorted(unconfirmed, key=lambda m: (m.kids or 0, m.score or 0))[-1:])
            if not pool:
                continue
            target = max(pool, key=lambda m: (m.kids or 0, m.score or 0))
            sources = [m for m in confirmed if m.id != target.id] + (parts if confirmed else []) + junk
            flagged = [m for m in unconfirmed if m.id != target.id]
            if sources:
                c["groups_merged"] += 1
            for s in sources:
                c["merged"] += 1
                if len(c["sample"]) < 60:
                    c["sample"].append({"taxon": binomial, "source": s.name, "target": target.name,
                                        "why": "имя испорчено" if s in junk else "часть вида" if s in parts
                                        else "оба имени есть в народных названиях вида",
                                        "vernacular": sorted(names)[:6]})
                if apply:
                    async with async_session() as db:
                        await merge_card(db, s.id, target.id, "site-sametaxon",
                                         f"same taxon {binomial}; vernacular: {', '.join(sorted(names)[:5])}")
                        await db.commit()
                    dead.append(str(s.id))
            for f in flagged:
                c["flagged"] += 1
                if len(c["flag_sample"]) < 40:
                    c["flag_sample"].append({"taxon": binomial, "card": f.name, "kept": target.name,
                                             "vernacular": sorted(names)[:6]})
                if apply:
                    async with async_session() as db:
                        await _finding(db, CHECK_MISMATCH, f.id,
                                       f"«{f.name}»: латынь {binomial}, но имя не из народных названий этого вида",
                                       {"name": f.name, "latin": f.name_latin, "taxon": binomial,
                                        "vernacular": sorted(names), "same_taxon_card": target.name})
                        await db.commit()
            if progress and n % 10 == 0:
                progress({k: v for k, v in c.items() if not isinstance(v, list)} | {"done": n})
    if dead:
        await _purge_qdrant(dead)
    return c


# ------------------------------------------------------------------ junkname

async def run_junkname(apply: bool, progress: Progress | None = None) -> dict:
    async with async_session() as db:
        rows = [r for r in await _published(db) if is_junk(r.name)]
    c = {"step": "junkname", "apply": apply, "candidates": len(rows), "renamed": 0, "left": 0,
         "sample": [], "left_sample": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        vern = Vernacular(client)
        for r in rows:
            genus, epi, _cap = latin_core(r.name_latin)
            new = None
            if genus and epi:
                cands = [v.strip() for v in await vern.names(f"{genus} {epi}")
                         if re.fullmatch(r"[А-ЯЁа-яё][а-яё -]+", v.strip())]
                # двусловное название вида раньше родового одного слова, короткое раньше длинного
                cands.sort(key=lambda v: (len(v.split()) != 2, len(v)))
                new = cands[0] if cands else None
                if new:
                    new = new[:1].upper() + new[1:]
                else:
                    # Русского названия нет: имя становится латынью вида. Такая карточка
                    # не проходит гейт публикации и уходит из атласа, но остаётся в поиске.
                    new = f"{genus} {epi}"
            if new:
                c["renamed"] += 1
                c["sample"].append({"old": r.name, "new": new, "latin": r.name_latin})
                if apply:
                    async with async_session() as db:
                        await _audit(db, "site-junkname", "rename", r.id, r.name, r.name_latin,
                                     extra={"new_name": new})
                        await db.execute(text("UPDATE plants SET name = :n, name_modern = COALESCE(name_modern, :n) "
                                              "WHERE id = :id AND name = :o"), {"n": new, "id": r.id, "o": r.name})
                        await db.commit()
            else:
                c["left"] += 1
                c["left_sample"].append({"name": r.name, "latin": r.name_latin})
    return c


# ------------------------------------------------------------------ mismatch

_MISMATCH_SYS = (
    "Ты ботаник-систематик. Дано название растения или гриба из старой русской книги, его нынешняя "
    "латынь, которая, скорее всего, ошибочна, кандидат, найденный по русскому названию, и выписки о "
    "применении. Определи, какой вид имеется в виду. Строго JSON: "
    "{\"latin\": \"Genus species\", \"confidence\": 0..100}. Если не уверен, confidence ниже 60."
)


def _name_match(card_name: str | None, other: str | None) -> bool:
    """Имя карточки совпадает с названием вида: два слова по первому и последнему, одно с одним."""
    a, b = _ru_words(card_name), _ru_words(other)
    if not a or not b:
        return False
    if len(a) >= 2 and len(b) >= 2:
        return ru_strong_match(card_name, other)
    return len(a) == 1 and len(b) == 1 and _stem(a[0]) == _stem(b[0])


async def _mismatch_context(db, pid) -> str:
    r = (await db.execute(text("""
        SELECT p.description,
          (SELECT string_agg(x, '; ') FROM (SELECT DISTINCT left(coalesce(u.original_text, u.action_raw, ''), 160) AS x
             FROM plant_medicinal_uses u WHERE u.plant_id = p.id LIMIT 4) q) AS uses,
          (SELECT string_agg(x, '; ') FROM (SELECT DISTINCT left(coalesce(h.original_text, h.biotope, ''), 120) AS x
             FROM plant_habitats h WHERE h.plant_id = p.id LIMIT 2) q) AS habitat
        FROM plants p WHERE p.id = :id"""), {"id": pid})).first()
    if not r:
        return ""
    return "\n".join(x for x in [
        f"Описание: {(r.description or '')[:500]}" if r.description else "",
        f"Применение: {r.uses}" if r.uses else "",
        f"Где растёт: {r.habitat}" if r.habitat else ""] if x)


async def _llm_agrees(name: str, latin: str | None, candidate: str, context: str) -> tuple[bool, dict]:
    from app.services.llm import chat_completion_json
    user = (f"Название: {name}\nНынешняя латынь: {latin}\nКандидат по русскому названию: {candidate}\n"
            f"{context}")
    try:
        res = await chat_completion_json([{"role": "system", "content": _MISMATCH_SYS},
                                          {"role": "user", "content": user}],
                                         task="plant_extraction", temperature=0.0, max_tokens=200)
    except Exception as e:  # noqa: BLE001 — сбой модели не валит прогон, карточка просто остаётся
        return False, {"error": type(e).__name__}
    if not isinstance(res, dict):
        return False, {"raw": str(res)[:200]}
    got = " ".join(str(res.get("latin") or "").split()[:2]).lower()
    conf = res.get("confidence") or 0
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0
    return (got == " ".join(candidate.split()[:2]).lower() and conf >= 80), {"latin": res.get("latin"), "conf": conf}


async def _refresh_taxon_data(client: httpx.AsyncClient, pid, new_latin: str, kingdom: str | None) -> str:
    """Фото, современное имя и семейство от нового вида. Старые были от чужого вида,
    поэтому без нового фото карточка остаётся без фото и уходит из атласа."""
    from app.services.inaturalist import _ICONIC_FOR_KINGDOM, _has_cyrillic, resolve_taxon_photo
    from app.services.photo_backfill import _mark_synced, _store_photo, fetch_taxon_photos, pick_licensed_photo

    fam = None
    d = await gbif_match(client, new_latin, "Fungi" if (kingdom or "").startswith("гриб") else "Plantae")
    if d and d.get("family"):
        fam = d["family"]
    async with async_session() as db:
        await db.execute(text("""
            UPDATE plants SET photo_url = NULL, photo_attribution = NULL, photo_license = NULL, photo_source = NULL,
                   inat_taxon_id = NULL, inat_synced_at = NULL, name_modern = NULL,
                   family_latin = COALESCE(CAST(:fam AS text), family_latin),
                   family = CASE WHEN CAST(:fam AS text) IS NULL THEN family END
            WHERE id = :id"""), {"id": pid, "fam": fam})
        await db.execute(text("""
            UPDATE plant_reader_monograph SET monograph = monograph || jsonb_build_object(
                'name_latin', CAST(:l AS text), 'photo_url', NULL, 'photo_attribution', NULL,
                'photo_license', NULL, 'photo_source', NULL)
            WHERE plant_id = :id"""), {"id": pid, "l": new_latin})
        await db.commit()
    iconic = _ICONIC_FOR_KINGDOM.get(kingdom or "растение", "Plantae")
    res = await resolve_taxon_photo(client, new_latin, iconic=iconic)
    await asyncio.sleep(1.5)
    if not res or not res.get("taxon_id"):
        return "фото нового вида не найдено"
    common = res.get("common_name")
    name_modern = common if _has_cyrillic(common) else None
    picked = None
    if res.get("photo_url"):
        picked = {"photo_url": res["photo_url"], "photo_attribution": res.get("photo_attribution"),
                  "photo_license": res.get("photo_license")}
    else:
        photos = await fetch_taxon_photos(client, res["taxon_id"])
        await asyncio.sleep(1.5)
        picked = pick_licensed_photo(photos or [])
    if picked:
        await _store_photo(pid, picked, "inaturalist", taxon_id=res["taxon_id"], name_modern=name_modern)
        return "фото нового вида"
    await _mark_synced(pid, taxon_id=res["taxon_id"], name_modern=name_modern)
    return "у нового вида нет фото со свободной лицензией"


async def _close_finding(pid, status: str, note: str, extra: dict) -> None:
    async with async_session() as db:
        await db.execute(text("""
            UPDATE data_quality_findings SET status = :st, resolved_by = 'identity-site', resolved_at = now(),
                   note = :note, evidence = COALESCE(evidence, '{}'::jsonb) || CAST(:ex AS jsonb)
            WHERE check_id = :c AND entity_id = :e"""),
            {"st": status, "note": note[:300], "ex": json.dumps({"mismatch": extra}, ensure_ascii=False, default=str),
             "c": CHECK_MISMATCH, "e": str(pid)})
        await db.commit()


async def run_mismatch(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    """Латынь по русскому имени для карточек, чьё имя не подтвердилось народными
    названиями их латыни (находки identity.site_mismatch). Вид берётся из iNaturalist и
    народных названий GBIF при совпадении имени по первому и последнему слову и
    подтверждается GBIF как вид того же царства. Тот же вид, что уже стоит: латынь верна,
    находка закрывается. Другой вид того же рода: латынь меняется по одному источнику.
    Другой род: нужны оба источника или согласие модели с уверенностью от 80."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT p.id, p.name, p.name_latin, p.kingdom FROM data_quality_findings f
            JOIN plants p ON p.id::text = f.entity_id
            WHERE f.check_id = :c AND f.status = 'open' ORDER BY p.name"""), {"c": CHECK_MISMATCH})).all()
    if limit:
        rows = rows[:limit]
    c = {"step": "mismatch", "apply": apply, "cards": len(rows), "relatin": 0, "confirmed": 0, "left": 0,
         "llm_yes": 0, "llm_no": 0, "sample": [], "confirmed_sample": [], "left_sample": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        for n, r in enumerate(rows, 1):
            head = re.split(r",|\(|\sили\s|;", r.name or "")[0].strip()
            cur_genus, cur_epi, _cap = latin_core(r.name_latin)
            cur_acc = await _gbif_species(client, f"{cur_genus} {cur_epi}", r.kingdom) if cur_genus and cur_epi else None
            cands: dict[str, set] = defaultdict(set)
            hints: dict[str, str] = {}
            for t in await _inat_species_by_ru(client, head):
                tname, tru = t.get("name") or "", t.get("preferred_common_name") or ""
                if tru and _name_match(head, tru) and len(tname.split()) >= 2:
                    acc = await _gbif_species(client, tname, r.kingdom)
                    if acc:
                        cands[acc].add("iNaturalist")
                        hints[acc] = tru
            for sci, vru in await _gbif_by_vernacular(client, head):
                if _name_match(head, vru):
                    acc = await _gbif_species(client, sci, r.kingdom)
                    if acc:
                        cands[acc].add("GBIF")
                        hints.setdefault(acc, vru)
            ranked = sorted(cands.items(), key=lambda kv: -len(kv[1]))
            decision, why, best = "left", "по русскому имени вид не найден", None
            if ranked:
                best, srcs = ranked[0]
                tie = len(ranked) > 1 and len(ranked[1][1]) == len(srcs)
                same_as_now = bool(cur_acc) and best.split()[:2] == cur_acc.split()[:2]
                if same_as_now:
                    decision, why = "confirmed", f"имя подтверждает нынешнюю латынь ({', '.join(sorted(srcs))})"
                elif tie:
                    decision, why = "left", "по имени нашлось несколько видов: " + ", ".join(k for k, _ in ranked[:3])
                elif cur_genus and best.split()[0].lower() == cur_genus.lower():
                    decision, why = "relatin", f"тот же род, {', '.join(sorted(srcs))}: {hints.get(best)}"
                elif len(srcs) >= 2:
                    decision, why = "relatin", f"другой род, оба источника: {hints.get(best)}"
                else:
                    async with async_session() as db:
                        ctx = await _mismatch_context(db, r.id)
                    ok, llm = await _llm_agrees(r.name, r.name_latin, best, ctx)
                    c["llm_yes" if ok else "llm_no"] += 1
                    decision = "relatin" if ok else "left"
                    why = (f"другой род, {', '.join(sorted(srcs))} и модель ({llm.get('conf')}): {hints.get(best)}" if ok
                           else f"другой род, один источник, модель не подтвердила ({llm.get('latin')}, {llm.get('conf')})")
            item = {"name": r.name, "old": r.name_latin, "new": best if decision == "relatin" else None, "why": why}
            if decision == "relatin":
                c["relatin"] += 1
                if len(c["sample"]) < 120:
                    c["sample"].append(item)
                if apply:
                    async with async_session() as db:
                        await _audit(db, "site-mismatch", "relatin", r.id, r.name, r.name_latin,
                                     extra={"new_latin": best, "why": why})
                        await db.execute(text("UPDATE plants SET name_latin = :new, safety_level = NULL WHERE id = :id"),
                                         {"new": best, "id": r.id})
                        await db.commit()
                    photo = await _refresh_taxon_data(client, r.id, best, r.kingdom)
                    await _close_finding(r.id, "fixed", f"латынь исправлена: {best}; {photo}", item | {"photo": photo})
            elif decision == "confirmed":
                c["confirmed"] += 1
                if len(c["confirmed_sample"]) < 60:
                    c["confirmed_sample"].append(item)
                if apply:
                    await _close_finding(r.id, "dismissed", "латынь верна: " + why, item)
            else:
                c["left"] += 1
                if len(c["left_sample"]) < 80:
                    c["left_sample"].append(item)
            if progress and n % 5 == 0:
                progress({k: v for k, v in c.items() if not isinstance(v, list)} | {"done": n})
    return c


STEPS = {"stale": run_stale, "oldspell": run_oldspell, "genuslatin": run_genuslatin,
         "sametaxon": run_sametaxon, "junkname": run_junkname, "mismatch": run_mismatch}


async def run_site_step(step: str, apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    fn = STEPS[step]
    if step in ("genuslatin", "sametaxon", "mismatch"):
        return await fn(apply=apply, limit=limit, progress=progress)
    return await fn(apply=apply, progress=progress)
