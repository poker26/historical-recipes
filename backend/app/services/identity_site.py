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
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from sqlalchemy import literal_column, select, text

from app.database import async_session
from app.models.plant import Plant
from app.services.identity_cleanup import (
    FACTS_SCORE, GBIF_PACE, GBIF_SPECIES, _audit, _kingdom_ok, _purge_qdrant, gbif_accepted, gbif_match,
    merge_card,
)
from app.services import site_cache
from app.services.identity_resolve import Vernacular, inat_taxa

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
    """Слово без окончания (двух последних букв), не короче трёх: «мяты» и «мята» дают
    «мят», а «крупнолистный» и «крупноплодный» остаются разными (первые шесть букв у них
    общие, и прежнее правило их путало: «Вяз крупнолистный» ушёл в вяз крупноплодный)."""
    return w[:max(3, len(w) - 2)]


def ru_strong_match(card_name: str | None, other: str | None) -> bool:
    """Первое и последнее слово совпадают по основе: «Цикута ядовитая» = «цикута ядовитая»."""
    a, b = _ru_words(card_name), _ru_words(other)
    if len(a) < 2 or len(b) < 2:
        return False
    return _stem(a[0]) == _stem(b[0]) and _stem(a[-1]) == _stem(b[-1])


# Народные названия в iNaturalist записаны через «ё», и поиск «Фиалка пестрая» там ничего
# не находит, а «Фиалка пёстрая» находит вид. Для самых частых слов строится вариант с «ё».
# Прилагательное меняется только целиком с окончанием: «пестролистный» и «черноплодный»
# пишутся без «ё».
_YO_STEMS = {"черн": "чёрн", "желт": "жёлт", "зелен": "зелён", "пестр": "пёстр", "тверд": "твёрд",
             "жестк": "жёстк", "темн": "тёмн", "тополев": "тополёв", "звездчат": "звёздчат",
             "трехцветн": "трёхцветн", "трехлистн": "трёхлистн", "четырехлистн": "четырёхлистн"}
_YO_ENDINGS = {"ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие"}
_YO_NOUNS = {"береза": "берёза", "березка": "берёзка", "клен": "клён", "еж": "ёж", "елка": "ёлка",
             "лен": "лён", "костер": "костёр", "мед": "мёд"}


def _yo_word(w: str) -> str:
    low = w.lower()
    new = _YO_NOUNS.get(low)
    if new is None:
        for stem, yo in _YO_STEMS.items():
            if low.startswith(stem) and low[len(stem):] in _YO_ENDINGS:
                new = yo + low[len(stem):]
                break
    if new is None:
        return w
    return new[0].upper() + new[1:] if w[0].isupper() else new


def yo_variant(name: str | None) -> str | None:
    """Имя с «ё» в частых словах («Фиалка пестрая» → «Фиалка пёстрая») или None, если
    менять нечего."""
    if not name:
        return None
    new = "".join(_yo_word(p) if p and p[0].isalpha() else p for p in re.split(r"([^А-Яа-яЁё]+)", name))
    return new if new != name else None


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


async def _sync_monograph(db, pid, fields: dict) -> None:
    """Имя, латынь и семейство страница карточки берёт из очерка. Шаг, который меняет их в
    карточке, меняет и очерк, иначе на сайте остаётся прежнее (так было с переименованиями
    29.09: в базе «Аистник», на странице «Аистникъ»)."""
    await db.execute(text("UPDATE plant_reader_monograph SET monograph = monograph || CAST(:p AS jsonb), "
                          "updated_at = now() WHERE plant_id = :id"),
                     {"p": json.dumps(fields, ensure_ascii=False), "id": pid})
    site_cache.mark(pid)   # кэш страницы сбросится после коммита, в конце шага


async def _published(db, *cols):
    score = literal_column(FACTS_SCORE.replace("p.id", "plants.id")).label("score")
    kids = literal_column("(SELECT count(*) FROM plants c WHERE c.parent_id = plants.id)").label("kids")
    from app.routers.plants import PUBLISHED_PRED
    return (await db.execute(select(
        Plant.id, Plant.name, Plant.name_latin, Plant.kingdom, Plant.rank, Plant.names_historical,
        score, kids, *cols).where(PUBLISHED_PRED))).all()


async def _finding(db, check_id: str, pid, title: str, evidence: dict) -> None:
    # Находка, закрытая как «латынь верна» (dismissed, в том числе после ручной сверки с
    # источником 30.09), при повторном срабатывании проверки не открывается снова:
    # иначе каждый прогон sametaxon возвращал бы в работу уже проверенные карточки.
    await db.execute(text("""
        INSERT INTO data_quality_findings
          (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
           auto_fixable, status, first_seen, last_seen)
        VALUES (CAST(:id AS uuid), :cid, 'P1', 'plant', :eid, :title, CAST(:ev AS jsonb),
                CAST(:fix AS jsonb), false, 'open', now(), now())
        ON CONFLICT (check_id, entity_id) DO UPDATE SET
          title = EXCLUDED.title, last_seen = now(),
          evidence = COALESCE(data_quality_findings.evidence, '{}'::jsonb) || EXCLUDED.evidence,
          status = CASE WHEN data_quality_findings.status = 'dismissed' THEN 'dismissed' ELSE 'open' END"""),
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
                await _sync_monograph(db, uuid.UUID(p["id"]), {"name": p["new"]})
            await db.commit()
    return result


# ------------------------------------------------------------------ genuslatin

class InatUnavailable(Exception):
    """iNaturalist не ответил и после повторов. Решение по карточке откладывается: пустой
    ответ нельзя читать как «вида с таким именем нет»."""


async def _inat_species_by_ru(client: httpx.AsyncClient, ru: str) -> list[dict]:
    try:
        res = await inat_taxa(client, {"q": ru, "locale": "ru", "per_page": 8, "rank": "species"})
    finally:
        await asyncio.sleep(1.2)
    if res is None:
        raise InatUnavailable(ru)
    return res


async def _inat_split(client: httpx.AsyncClient, inat_taxon: tuple[str, int | None] | None,
                      cur_genus: str | None, cur_epi: str | None) -> str | None:
    """Бином iNaturalist, найденный по имени карточки, если GBIF свёл его в нынешний вид, а
    iNaturalist держит нынешний бином отдельным действующим видом. None, если это то же
    имя или настоящий синоним (iNaturalist ведёт оба бинома к одному виду)."""
    if not inat_taxon or not cur_genus or not cur_epi:
        return None
    tname, tid = inat_taxon
    binom = " ".join(tname.split()[:2])
    cur = f"{cur_genus} {cur_epi}"
    if binom.lower() == cur.lower():
        return None
    for t in await _inat_species_by_ru(client, cur):
        if (t.get("name") or "").lower() == cur.lower() and t.get("is_active", True) and t.get("id") != tid:
            return binom
    return None


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
                try:
                    inat = await _inat_species_by_ru(client, ru)
                    yo = yo_variant(ru)
                    if yo and not any(ru_strong_match(r.name, t.get("preferred_common_name")) for t in inat):
                        inat += await _inat_species_by_ru(client, yo)
                except InatUnavailable:
                    continue  # без ответа iNaturalist карточку не трогаем и находку не пишем
                for t in inat:
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
                        await _sync_monograph(db, r.id, {"name_latin": new})
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
                        await _sync_monograph(db, r.id, {"name": new})
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


_PROPOSE_SYS = (
    "Ты ботаник-систематик. Дано название растения или гриба из старой русской книги и выписки о "
    "применении. Назови вид, который обозначает это русское название в ботанической литературе. Строго JSON: "
    "{\"latin\": \"Genus species\", \"confidence\": 0..100}. Если название неоднозначно или не известно, "
    "confidence ниже 60."
)


async def _llm_propose(name: str, latin: str | None, context: str) -> dict:
    from app.services.llm import chat_completion_json
    # Нынешнюю латынь модели не показываем: с ней перед глазами модель соглашалась и с
    # неверной («Корица китайская» с латынью цейлонской корицы).
    user = f"Название: {name}\n{context}"
    try:
        res = await chat_completion_json([{"role": "system", "content": _PROPOSE_SYS},
                                          {"role": "user", "content": user}],
                                         task="plant_extraction", temperature=0.0, max_tokens=200)
    except Exception as e:  # noqa: BLE001 — сбой модели не валит прогон, карточка остаётся
        return {"error": type(e).__name__}
    if not isinstance(res, dict):
        return {}
    try:
        conf = float(res.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    latin_p = " ".join(str(res.get("latin") or "").split()[:2])
    return {"latin": latin_p, "conf": conf}


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
        # Русское имя семейства было от прежнего вида. Берётся то, что стоит у других
        # карточек этого семейства, иначе страница остаётся без семейства.
        fam_ru = (await db.execute(text(
            "SELECT mode() WITHIN GROUP (ORDER BY family) FROM plants "
            "WHERE family_latin = CAST(:fam AS text) AND family IS NOT NULL"), {"fam": fam})).scalar() if fam else None
        await db.execute(text("""
            UPDATE plants SET photo_url = NULL, photo_attribution = NULL, photo_license = NULL, photo_source = NULL,
                   inat_taxon_id = NULL, inat_synced_at = NULL, name_modern = NULL,
                   family_latin = COALESCE(CAST(:fam AS text), family_latin),
                   family = CASE WHEN CAST(:fam AS text) IS NULL THEN family ELSE CAST(:fam_ru AS text) END
            WHERE id = :id"""), {"id": pid, "fam": fam, "fam_ru": fam_ru})
        patch = {"name_latin": new_latin, "photo_url": None, "photo_attribution": None,
                 "photo_license": None, "photo_source": None}
        if fam:
            patch |= {"family_latin": fam, "family": fam_ru}
        await _sync_monograph(db, pid, patch)
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


async def _close_finding(pid, status: str, note: str, extra: dict,
                         check: str = CHECK_MISMATCH, key: str = "mismatch") -> None:
    async with async_session() as db:
        await db.execute(text("""
            UPDATE data_quality_findings SET status = :st, resolved_by = 'identity-site', resolved_at = now(),
                   note = :note, evidence = COALESCE(evidence, '{}'::jsonb) || CAST(:ex AS jsonb)
            WHERE check_id = :c AND entity_id = :e"""),
            {"st": status, "note": note[:300], "ex": json.dumps({key: extra}, ensure_ascii=False, default=str),
             "c": check, "e": str(pid)})
        await db.commit()


async def run_mismatch(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    """Латынь по русскому имени для карточек, чьё имя не подтвердилось народными
    названиями их латыни (находки identity.site_mismatch). Вид берётся из iNaturalist и
    народных названий GBIF при совпадении имени по первому и последнему слову и
    подтверждается GBIF как вид того же царства. Имя ищется и в написании через «ё».
    Тот же вид, что уже стоит: латынь верна, находка закрывается; но если GBIF лишь свёл
    найденный вид в нынешний, а iNaturalist их различает, латынь меняется на найденный вид.
    Другой вид того же рода: латынь меняется по одному источнику. Другой род: нужны оба
    источника или согласие модели с уверенностью от 80. Если поиск ничего не дал, вид
    предлагает модель, а принимается он только при совпадении имени карточки с народным
    названием вида, в том числе когда модель называет нынешний вид."""
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
        vern = Vernacular(client)
        for n, r in enumerate(rows, 1):
            head = re.split(r",|\(|\sили\s|;", r.name or "")[0].strip()
            cur_genus, cur_epi, _cap = latin_core(r.name_latin)
            cur_acc = await _gbif_species(client, f"{cur_genus} {cur_epi}", r.kingdom) if cur_genus and cur_epi else None
            cands: dict[str, set] = defaultdict(set)
            hints: dict[str, str] = {}
            inat_ids: dict[str, tuple[str, int | None]] = {}  # принятый бином GBIF → (бином и id iNaturalist)
            yo = yo_variant(head)
            inat_down = False
            try:
                inat = await _inat_species_by_ru(client, head)
                if yo and not any(_name_match(head, t.get("preferred_common_name")) for t in inat):
                    inat += await _inat_species_by_ru(client, yo)
            except InatUnavailable:
                inat, inat_down = [], True
            for t in inat:
                tname, tru = t.get("name") or "", t.get("preferred_common_name") or ""
                if tru and _name_match(head, tru) and len(tname.split()) >= 2:
                    acc = await _gbif_species(client, tname, r.kingdom)
                    if acc:
                        cands[acc].add("iNaturalist")
                        hints[acc] = tru
                        inat_ids.setdefault(acc, (tname, t.get("id")))
            gbif_v = await _gbif_by_vernacular(client, head)
            if yo and not any(_name_match(head, v) for _, v in gbif_v):
                gbif_v += await _gbif_by_vernacular(client, yo)
            for sci, vru in gbif_v:
                if _name_match(head, vru):
                    acc = await _gbif_species(client, sci, r.kingdom)
                    if acc:
                        cands[acc].add("GBIF")
                        hints.setdefault(acc, vru)
            ranked = sorted(cands.items(), key=lambda kv: -len(kv[1]))
            decision, why, best = "left", "по русскому имени вид не найден", None
            if inat_down:
                why = "iNaturalist не ответил, карточка отложена до следующего прохода"
            elif ranked:
                best, srcs = ranked[0]
                tie = len(ranked) > 1 and len(ranked[1][1]) == len(srcs)
                same_as_now = bool(cur_acc) and best.split()[:2] == cur_acc.split()[:2]
                try:
                    split = await _inat_split(client, inat_ids.get(best), cur_genus, cur_epi) if same_as_now else None
                except InatUnavailable:
                    split, inat_down = None, True
                if inat_down:
                    decision, best = "left", None
                    why = "iNaturalist не ответил при сверке видов, карточка отложена до следующего прохода"
                elif split:
                    # GBIF сводит вид, который называет имя карточки, в нынешний, а iNaturalist
                    # держит их разными видами («Подгруздок чернеющий» Russula nigricans и
                    # подгруздок чёрный R. adusta). Имя карточки называет именно этот вид.
                    hint = hints.get(best)
                    if split.split()[0].lower() == (cur_genus or "").lower():
                        decision, best = "relatin", split
                        why = f"GBIF сводит {split} в нынешний вид, iNaturalist их различает: {hint}"
                    else:
                        decision, why = "left", f"GBIF сводит {split} в нынешний вид, iNaturalist различает, род другой"
                elif same_as_now:
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
            if decision == "left" and not ranked and not inat_down:
                # Поиск по имени ничего не дал: вид предлагает модель, а проверяет обратный путь,
                # народные названия предложенного вида должны содержать имя карточки.
                async with async_session() as db:
                    ctx = await _mismatch_context(db, r.id)
                prop = await _llm_propose(r.name, r.name_latin, ctx)
                acc = None
                if prop.get("latin") and prop.get("conf", 0) >= 80:
                    acc = await _gbif_species(client, prop["latin"], r.kingdom, exact_only=True)
                if not acc:
                    why = f"поиск не нашёл, модель не уверена или GBIF не знает ({prop.get('latin')}, {prop.get('conf')})"
                elif cur_acc and acc.split()[:2] == cur_acc.split()[:2]:
                    # Одной модели мало: описание карточки бывает от другого вида, и модель
                    # соглашается с ним («Корица китайская» с описанием цейлонской корицы).
                    # Латынь верна, только если имя карточки есть среди народных названий вида.
                    names = await vern.names(acc)
                    hit = next((v for v in names if _name_match(head, v)), None)
                    if hit and prop["conf"] >= 90:
                        decision, why = "confirmed", f"модель ({prop['conf']}) и народное название «{hit}» подтверждают нынешнюю латынь"
                    elif hit:
                        why = f"модель за нынешнюю латынь, но уверенность {prop['conf']}"
                    else:
                        why = f"модель за нынешнюю латынь ({prop['conf']}), но среди народных названий вида нет «{head}»"
                else:
                    names = await vern.names(acc)
                    hit = next((v for v in names if _name_match(head, v)), None)
                    if hit:
                        decision, best = "relatin", acc
                        why = f"модель ({prop['conf']}), народное название вида: {hit}"
                    else:
                        why = f"модель предлагает {acc}, но среди народных названий вида нет «{head}»"
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


# ------------------------------------------------------------------ drift

CHECK_DRIFT = "identity.site_drift"


def latin_shape(latin: str | None) -> str:
    """«вид», «род», «сокращение» («A. eichwaldii») или «мусор» (кириллица, цифры, пусто)."""
    s = (latin or "").strip()
    # Мусор ищется только в роде и эпитете: искажённый автор («Vosk. et Sin8.») вид не портит.
    head = " ".join(s.split()[:2])
    if not s or _CYR.search(head) or re.search(r"[0-9$|{}\[\]]", head):
        return "мусор"
    genus, epi, _cap = latin_core(s)
    if not genus:
        return "сокращение" if re.match(r"^[A-Z]\.\s*[a-z]", s) else "мусор"
    return "вид" if epi else "род"


def _in_quotes(latin: str | None, quotes: str) -> bool:
    """Видовой эпитет латыни стоит целым словом в цитатах карточки (они в нижнем регистре)."""
    genus, epi, _cap = latin_core(latin)
    if not genus or not epi or len(epi) < 4:
        return False
    return re.search(r"(?<![a-z])" + re.escape(epi) + r"(?![a-z])", quotes) is not None


# Русское слово, которым называют сразу несколько научных родов: по нему род не ставится
# («горец» это Persicaria, Polygonum, Aconogonon и Bistorta; «плаун» это и Diphasiastrum).
_POLYSEMOUS_GENUS_WORDS = {"горец", "трутовик", "ромашка", "осот", "кипрей", "плаун", "папоротник", "мох",
                           "лишайник", "гриб", "губка", "трава", "корень", "дерево", "кустарник", "водоросль",
                           "дудник", "камыш", "пузырник", "лапчатка", "марь", "бурачок", "василек"}


async def _genus_consistent(vern: "Vernacular", card_name: str | None, latin: str | None) -> bool:
    """Род латыни не противоречит первому слову имени: совпадает с народным названием рода,
    или у рода нет русского названия, или слово многозначное."""
    genus, _epi, _cap = latin_core(latin)
    words = _ru_words(card_name)
    if not genus or not words or words[0] in _POLYSEMOUS_GENUS_WORDS:
        return True
    gnames = await vern.names(genus)
    return not gnames or _genus_name_match(card_name, gnames)


def _genus_name_match(card_name: str | None, names: set[str]) -> bool:
    """Первое слово имени карточки совпадает с первым словом народного названия рода."""
    a = _ru_words(card_name)
    if not a:
        return False
    return any(_ru_words(v) and _stem(a[0]) == _stem(_ru_words(v)[0]) for v in names)


async def _card_quotes(db, pid) -> str:
    q = (await db.execute(text("""
        SELECT string_agg(lower(left(x, 600)), ' ') AS qt FROM (
          SELECT original_text AS x FROM plant_book_mentions WHERE plant_id = :id
          UNION ALL SELECT original_text FROM plant_medicinal_uses WHERE plant_id = :id
          UNION ALL SELECT original_text FROM plant_habitats WHERE plant_id = :id) s
        WHERE x IS NOT NULL"""), {"id": pid})).scalar()
    return (q or "")[:300000]


async def _gbif_genus(client: httpx.AsyncClient, genus: str, card_kingdom: str | None) -> str | None:
    kingdom = "Fungi" if (card_kingdom or "").startswith("гриб") else "Plantae"
    d = await gbif_match(client, genus, kingdom)
    await asyncio.sleep(GBIF_PACE)
    if not d or d.get("matchType") != "EXACT" or d.get("rank") != "GENUS" or not _kingdom_ok(card_kingdom, d.get("kingdom")):
        return None
    return d.get("canonicalName")


async def _genus_by_ru_word(client: httpx.AsyncClient, word: str, card_kingdom: str | None) -> tuple[str, str] | None:
    """Род, чьё русское название в iNaturalist совпадает со словом («ситник» → Juncus)."""
    try:
        res = await inat_taxa(client, {"q": word, "locale": "ru", "per_page": 5, "rank": "genus"})
    finally:
        await asyncio.sleep(1.2)
    if res is None:
        raise InatUnavailable(word)
    for t in res:
        tru, tname = t.get("preferred_common_name") or "", t.get("name") or ""
        if tru and _ru_words(tru) and _stem(_ru_words(tru)[0]) == _stem(word) and len(tname.split()) == 1:
            gen = await _gbif_genus(client, tname, card_kingdom)
            if gen:
                return gen, tru
    return None


async def _candidates_by_name(client: httpx.AsyncClient, head: str, kingdom: str | None):
    """Виды по русскому имени (iNaturalist и народные названия GBIF), как в mismatch."""
    cands: dict[str, set] = defaultdict(set)
    hints: dict[str, str] = {}
    yo = yo_variant(head)
    inat = await _inat_species_by_ru(client, head)
    if yo and not any(_name_match(head, t.get("preferred_common_name")) for t in inat):
        inat += await _inat_species_by_ru(client, yo)
    for t in inat:
        tname, tru = t.get("name") or "", t.get("preferred_common_name") or ""
        if tru and _name_match(head, tru) and len(tname.split()) >= 2:
            acc = await _gbif_species(client, tname, kingdom)
            if acc:
                cands[acc].add("iNaturalist")
                hints[acc] = tru
    gbif_v = await _gbif_by_vernacular(client, head)
    if yo and not any(_name_match(head, v) for _, v in gbif_v):
        gbif_v += await _gbif_by_vernacular(client, yo)
    for sci, vru in gbif_v:
        if _name_match(head, vru):
            acc = await _gbif_species(client, sci, kingdom)
            if acc:
                cands[acc].add("GBIF")
                hints.setdefault(acc, vru)
    return sorted(cands.items(), key=lambda kv: -len(kv[1])), hints


async def _drift_decide(client: httpx.AsyncClient, vern: Vernacular, r) -> tuple[str, str | None, str]:
    old, new = r.m_latin, r.name_latin
    if old == new:
        return "name_only", None, "поменялось только имя"
    head = re.split(r",|\(|\sили\s|;", r.name or "")[0].strip()
    async with async_session() as db:
        quotes = await _card_quotes(db, r.id)
    s_old, s_new = latin_shape(old), latin_shape(new)
    g_old, e_old, _ = latin_core(old) if s_old in ("вид", "род") else (None, None, False)
    g_new, e_new, _ = latin_core(new) if s_new in ("вид", "род") else (None, None, False)
    if g_old and g_new and g_old.lower() == g_new.lower() and e_old == e_new:
        return "sync", new, "та же латынь, другая запись"
    # Латинское имя («Bursa», «Chondrostereum purpureum») ставила сама чистка вместе с латынью:
    # в базе они согласованы, а русского имени, с которым сверять, нет.
    if not _CYR.search(r.name or ""):
        return "sync", new, "имя карточки латинское, в базе имя и латынь согласованы"
    q_new, q_old = _in_quotes(new, quotes), _in_quotes(old, quotes)
    if q_new:
        return "sync", new, "новая латынь стоит в цитатах источника"
    acc_new = await _gbif_species(client, f"{g_new} {e_new}", r.kingdom) if (g_new and e_new) else None
    acc_old = await _gbif_species(client, f"{g_old} {e_old}", r.kingdom) if (g_old and e_old) else None
    # Синонимия доказывает только, что прежняя и новая латынь одно и то же. Неверными
    # бывают обе («Щетинник зелёный»: Poa rubra → Eragrostis capillaris вместо Setaria
    # viridis), поэтому род ещё сверяется с первым словом имени.
    if (acc_new and acc_old and acc_new.split()[:2] == acc_old.split()[:2]
            and await _genus_consistent(vern, r.name, acc_new)):
        return "sync", new, f"синонимы по GBIF: {acc_new}"
    # Опечатка в прежней латыни («Crotolaria crispta»): GBIF её не знает, а новая отличается
    # на одну-две буквы. Имя карточки при этом должно хоть как-то совпасть с народными
    # названиями нового вида, иначе «Белянка» ушла бы в чужой млечник (albidus → alpinus).
    if (acc_new and not acc_old and g_old and e_old and e_new
            and Levenshtein.distance(g_old.lower(), g_new.lower()) <= 2
            and Levenshtein.distance(e_old, e_new) <= 2):
        if species_evidence(r.name, await vern.names(acc_new)) is not None:
            return "sync", new, "исправлена опечатка в прежней латыни"
    genus_contradicts = False
    if acc_new:
        if species_evidence(r.name, await vern.names(acc_new)) in ("name", "part"):
            return "sync", new, "имя карточки среди народных названий новой латыни"
    elif g_new and not e_new:
        gnames = await vern.names(g_new)
        if _genus_name_match(r.name, gnames):
            return "sync", new, "первое слово имени = народное название нового рода"
        genus_contradicts = bool(gnames)
    if acc_old:
        ev_old = species_evidence(r.name, await vern.names(acc_old))
        # Цитата может лишь упоминать чужой вид («в отличие от…»): прежний род не должен
        # противоречить первому слову имени карточки («Никандра» при Physalifolium).
        gnames_old = await vern.names(g_old)
        genus_ok = not gnames_old or _genus_name_match(r.name, gnames_old)
        if ev_old in ("name", "part") or (q_old and genus_ok):
            return "revert", old, ("имя карточки среди народных названий прежней латыни"
                                   if ev_old in ("name", "part") else "прежняя латынь стоит в цитатах источника")
    elif g_old and not e_old and s_new != "вид":
        if await _gbif_genus(client, g_old, r.kingdom) and _genus_name_match(r.name, await vern.names(g_old)):
            return "revert", old, "первое слово имени = народное название прежнего рода"
    # Ни одна не подтверждена: вид по русскому имени.
    ranked, hints = await _candidates_by_name(client, head, r.kingdom) if head else ([], {})
    words = _ru_words(r.name)
    ru_genus = None
    if ranked:
        best, srcs = ranked[0]
        tie = len(ranked) > 1 and len(ranked[1][1]) == len(srcs)
        if acc_new and best.split()[:2] == acc_new.split()[:2]:
            return "sync", new, f"поиск по имени даёт новую латынь ({', '.join(sorted(srcs))})"
        if acc_old and best.split()[:2] == acc_old.split()[:2]:
            return "revert", old, f"поиск по имени даёт прежнюю латынь ({', '.join(sorted(srcs))})"
        if not tie:
            bg = best.split()[0].lower()
            ok = bg in {(g_new or "").lower(), (g_old or "").lower()} or len(srcs) >= 2
            if not ok and words:
                ru_genus = await _genus_by_ru_word(client, words[0], r.kingdom)
                ok = bool(ru_genus) and ru_genus[0].lower() == bg
            if ok:
                return "relatin", best, f"по русскому имени ({', '.join(sorted(srcs))}): {hints.get(best)}"
    # Вида нет, но род по первому слову имени противоречит новой латыни: ставится род.
    # Имя, начатое прилагательным («солончаковая валериана»), род первым словом не называет.
    adjective_first = bool(words) and re.search(r"(ая|яя|ый|ий|ой|ое|ее|ые|ие)$", words[0]) is not None
    if words and not adjective_first and words[0] not in _POLYSEMOUS_GENUS_WORDS and (
            genus_contradicts or (acc_new and not _genus_name_match(r.name, await vern.names(g_new)))):
        ru_genus = ru_genus or await _genus_by_ru_word(client, words[0], r.kingdom)
        if ru_genus and ru_genus[0].lower() != (g_new or "").lower():
            return "genus", ru_genus[0], f"род по первому слову имени: {ru_genus[1]} = {ru_genus[0]}"
    return "review", None, f"не подтверждена ни прежняя ({s_old}), ни новая ({s_new}) латынь"


async def _drift_apply(client: httpx.AsyncClient, r, decision: str, latin: str | None, why: str) -> str:
    if decision in ("sync", "name_only"):
        async with async_session() as db:
            fam = (await db.execute(text("SELECT family, family_latin FROM plants WHERE id = :id"), {"id": r.id})).first()
            patch = {"name": r.name}
            if decision == "sync":
                patch |= {"name_latin": r.name_latin, "family": fam.family, "family_latin": fam.family_latin}
            await _sync_monograph(db, r.id, patch)
            await db.commit()
        return ""
    if decision in ("revert", "relatin", "genus"):
        async with async_session() as db:
            await _audit(db, "site-drift", "relatin", r.id, r.name, r.name_latin,
                         extra={"new_latin": latin, "why": why, "decision": decision, "page_latin": r.m_latin})
            await db.execute(text("UPDATE plants SET name_latin = :new, safety_level = NULL WHERE id = :id"),
                             {"new": latin, "id": r.id})
            await db.commit()
        photo = await _refresh_taxon_data(client, r.id, latin, r.kingdom)
        async with async_session() as db:
            await _sync_monograph(db, r.id, {"name": r.name})
            await db.commit()
        return photo
    if decision == "review":
        async with async_session() as db:
            await _finding(db, CHECK_DRIFT, r.id,
                           f"«{r.name}»: на странице {r.m_latin}, в карточке {r.name_latin}; ни одна латынь не подтверждена",
                           {"page_latin": r.m_latin, "card_latin": r.name_latin, "why": why})
            await db.commit()
    return ""


async def run_drift(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    """Очерк показывает латынь, которая была у карточки при его генерации, а чистка 25–26.09
    (reid, gbif, resolve) поменяла её у ~1 500 карточек только в базе. Шаг решает по каждой:
    новая латынь подтверждена — переносится в очерк; подтверждена прежняя — возвращается в
    карточку; иначе вид ищется по русскому имени, а если и он не найден, но первое слово имени
    называет другой род, ставится этот род. Свидетельства: латынь в цитатах самой карточки (у
    Анненкова и определителей она стоит в тексте статьи), имя карточки среди народных
    названий вида (GBIF, iNaturalist), синонимия GBIF. Модель не используется: одного её
    согласия мало. Неподтверждённое пишется находкой identity.site_drift, и генератор
    очерков такую карточку пропускает до разбора."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT p.id, p.name, p.name_latin, p.kingdom, p.rank,
                   m.monograph->>'name' AS m_name, m.monograph->>'name_latin' AS m_latin
            FROM plant_reader_monograph m JOIN plants p ON p.id = m.plant_id
            WHERE (m.monograph->>'name_latin' IS DISTINCT FROM p.name_latin
                   OR m.monograph->>'name' IS DISTINCT FROM p.name)
              -- открытая находка identity.site_drift: карточка ждёт ручного разбора, шаг её не трогает
              AND NOT EXISTS (SELECT 1 FROM data_quality_findings f WHERE f.check_id = :chk
                              AND f.status = 'open' AND f.entity_id = p.id::text)
            ORDER BY p.name"""), {"chk": CHECK_DRIFT})).all()
    if limit:
        rows = rows[:limit]
    c = {"step": "drift", "apply": apply, "cards": len(rows), "sync": 0, "revert": 0, "relatin": 0, "genus": 0,
         "review": 0, "name_only": 0, "deferred": 0, "items": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        vern = Vernacular(client)
        # Две карточки одновременно: больше даёт отказы iNaturalist (у него предел около
        # запроса в секунду, а бэкенд ходит туда же за наблюдениями).
        sem = asyncio.Semaphore(2)
        done = 0

        async def one(r) -> None:
            nonlocal done
            async with sem:
                try:
                    decision, latin, why = await _drift_decide(client, vern, r)
                except InatUnavailable:
                    decision, latin, why = "deferred", None, "iNaturalist не ответил, карточка отложена"
                photo = ""
                if apply and decision != "deferred":
                    photo = await _drift_apply(client, r, decision, latin, why)
                c[decision] += 1
                c["items"].append({"n": r.name, "o": r.m_latin, "c": r.name_latin, "d": decision, "l": latin,
                                   "w": why + (f"; {photo}" if photo else "")})
                done += 1
                if progress and done % 10 == 0:
                    progress({k: v for k, v in c.items() if k != "items"} | {"done": done})

        for i in range(0, len(rows), 20):
            await asyncio.gather(*(one(r) for r in rows[i:i + 20]))
    return c


# ------------------------------------------------------------------ driftreview

# Русское видовое слово → начала латинских эпитетов, которые оно переводит. Берётся самая
# длинная подходящая основа: «голубой» не проверяется как «голый», «сердцевидный» как «серый».
_EPI_STEMS = {
    # цвет
    "бел": ["alb", "candid", "leuc", "nive"], "беловат": ["albid", "albesc"], "черн": ["nigr", "melan", "atr"],
    "красн": ["rubr", "rubens", "erythr", "sanguin", "coccin"], "краснеющ": ["rubesc", "erubesc"],
    "желт": ["lute", "flav", "xanth", "citrin", "ochr"], "зелен": ["virid", "chlor"],
    "син": ["cyan", "caerul", "coerul", "azur"], "голуб": ["caerul", "coerul", "glauc", "cyan"],
    "сиз": ["glauc", "caes"], "сер": ["canesc", "cinere", "gris", "incan"], "сед": ["incan", "canesc"],
    "серебрист": ["argent"], "пурпур": ["purpur"], "розов": ["rose", "rhod"], "фиолетов": ["violac"],
    "золотист": ["aure", "chrys"], "оранжев": ["aurant"], "бур": ["fusc", "brunn"], "рыж": ["ruf"],
    "темн": ["obscur", "atrat"], "бледн": ["pallid"], "телесн": ["incarnat"], "пестр": ["varieg", "versicol"],
    "двуцветн": ["bicolor"], "трехцветн": ["tricolor"], "разноцветн": ["versicolor", "discolor"],
    # польза и свойства
    "лекарствен": ["officinal", "medicinal"], "аптеч": ["officinal"], "врачебн": ["officinal"],
    "обыкновен": ["vulgar", "commun"], "настоящ": ["ver", "genuin"], "съедобн": ["edul", "esculent"],
    "ядовит": ["viros", "toxic", "venen"], "красильн": ["tinctor"], "масличн": ["oleifer"], "мыльн": ["saponar"],
    "посевн": ["sativ"], "культурн": ["sativ", "cultivat", "cultus"], "огородн": ["hortens", "olerace"],
    "садов": ["hortens"], "сорн": ["ruderal"], "благородн": ["nobil"], "царск": ["regi", "regal"],
    "душист": ["odorat", "fragran", "suaveol"], "пахуч": ["graveol", "odor"], "ароматн": ["aromatic"],
    "вонюч": ["foetid", "fetid"], "горьк": ["amar"], "сладк": ["dulc"], "кисл": ["acid", "acetos"],
    "жгуч": ["uren", "acri"], "едк": ["acri"], "перечн": ["piperit"], "колюч": ["spinos", "acanth"],
    "клейк": ["glutinos", "viscos"], "липк": ["viscos", "glutinos"], "смолист": ["resinos"], "железист": ["glandulos"],
    # место
    "полев": ["arvens", "campestr"], "лесн": ["sylvat", "silvat", "sylvestr", "silvestr", "nemoral", "nemoros"],
    "дубравн": ["nemoros", "nemoral"], "лугов": ["pratens"], "болотн": ["palustr", "uliginos", "paludos"],
    "водн": ["aquatic"], "водян": ["aquatic"], "речн": ["fluviat", "ripar"], "приречн": ["ripar"],
    "ручейков": ["rivular"], "прибрежн": ["ripar", "littoral", "litoral"], "горн": ["montan", "alpin", "orophil"],
    "альпийск": ["alpin"], "песчан": ["arenar", "sabulos"], "степн": ["stepp"], "приморск": ["maritim", "littoral"],
    "морск": ["marin", "maritim"], "каменист": ["saxatil", "rupestr", "petrae"], "скальн": ["rupestr", "saxatil"],
    "солончаков": ["salin", "halophil"],
    # облик
    "ползуч": ["repen", "reptan"], "стелющ": ["prostrat", "procumb", "humifus"], "лежач": ["procumb", "prostrat", "decumb"],
    "прям": ["erect", "strict"], "прямостояч": ["erect", "strict"], "вьющ": ["volubil", "scanden"],
    "плакуч": ["pendul"], "повисл": ["pendul"], "поникающ": ["nutan", "cernu"], "поникш": ["nutan", "cernu"],
    "высок": ["elat", "excels", "procer", "altissim"], "низк": ["humil", "pumil"], "карликов": ["nan", "pumil", "pygmae"],
    "больш": ["major", "magn", "maxim", "grand"], "мал": ["minor", "minim", "parv"], "средн": ["medi", "intermedi"],
    "гигантск": ["gigant"], "кустарников": ["frutic"], "древовидн": ["arbore", "arboresc"],
    "волосист": ["hirsut", "pilos", "villos", "crinit"], "опушен": ["pubesc"], "пушист": ["pubesc", "tomentos"],
    "мохнат": ["hirsut", "villos"], "шерстист": ["lanat", "lanug"], "войлоч": ["tomentos"], "шелковист": ["serice"],
    "бархатист": ["velutin"], "гол": ["glabr", "nud"], "щетинист": ["setos", "hispid"], "шершав": ["scabr", "asper"],
    "шероховат": ["scabr", "asper"], "жестк": ["rigid"], "мягк": ["moll"], "нежн": ["tenell"], "тонк": ["tenu", "gracil"],
    "изящн": ["elegan", "gracil"], "красив": ["pulchr", "pulchell", "specios", "formos"], "великолепн": ["magnific", "splendid"],
    "блестящ": ["lucid", "nitid", "splenden"], "крылат": ["alat"], "рогат": ["cornut"], "бородат": ["barbat"],
    "колосист": ["spicat"], "метельчат": ["paniculat"], "зонтичн": ["umbellat"], "головчат": ["capitat"],
    "щитков": ["corymbos"], "кистист": ["racemos"], "мутовчат": ["verticillat"], "ветвист": ["ramos"],
    "раскидист": ["diffus", "patul"], "пальчат": ["digitat", "palmat"], "перист": ["pinnat"], "рассечен": ["dissect", "laciniat"],
    "лопаст": ["lobat", "laciniat"], "сердцевидн": ["cordat"], "округл": ["rotund"], "яйцевидн": ["ovat"],
    "копьевидн": ["hastat"], "стреловидн": ["sagittat"], "ланцетн": ["lanceolat"], "туп": ["obtus"],
    "остр": ["acut"], "пятнист": ["maculat"], "полосат": ["striat"], "луковичн": ["bulbos", "bulbifer"],
    "клубнев": ["tuberos"], "клубненосн": ["tuberos"], "узколист": ["angustifol"], "широколист": ["latifol", "platyphyll"],
    "мелколист": ["microphyll", "parvifol"], "крупнолист": ["macrophyll", "grandifol"], "тонколист": ["tenuifol"],
    "длиннолист": ["longifol"], "округлолист": ["rotundifol"], "мелкоцвет": ["parviflor", "micranth"],
    "крупноцвет": ["grandiflor", "macranth"], "многоцвет": ["multiflor", "polyanth"], "малоцвет": ["pauciflor"],
    "одноцветков": ["uniflor"], "крупноплодн": ["macrocarp"], "мелкоплодн": ["microcarp"], "трехлист": ["trifol", "triphyll"],
    "пятилист": ["quinquefol"], "двулист": ["bifol"], "двудомн": ["dioic"], "однодомн": ["monoic"],
    "однобок": ["secund"], "ложн": ["pseud"], "сомнительн": ["dubi"], "изменчив": ["variabil", "mutabil"],
    # время
    "ранн": ["praecox"], "поздн": ["serotin"], "весенн": ["vern"], "летн": ["aestiv"], "осенн": ["autumn"],
    "зимн": ["hyemal", "hiemal"], "однолетн": ["annu"], "многолетн": ["perenn"], "двулетн": ["bienn"], "ночн": ["noct"],
    # география
    "восточн": ["oriental"], "западн": ["occidental"], "северн": ["boreal", "septentrional"], "южн": ["austral", "meridional"],
    "китайск": ["chinens", "sinens"], "японск": ["japonic"], "корейск": ["corean", "koraiens"], "кавказск": ["caucas"],
    "сибирск": ["sibiric"], "даурск": ["dahuric", "davuric", "dauric"], "алтайск": ["altaic"], "уральск": ["uralens"],
    "крымск": ["tauric"], "амурск": ["amurens"], "уссурийск": ["ussuriens"], "камчатск": ["kamtschat", "camtschat"],
    "туркестанск": ["turkestan"], "джунгарск": ["songar", "soongar", "dzhungar"], "персидск": ["persic"],
    "армянск": ["armen"], "грузинск": ["georgic", "iberic"], "европейск": ["europae"], "американск": ["americ"],
    "виргинск": ["virgin"], "канадск": ["canadens"], "индийск": ["indic"], "египетск": ["aegypt"], "понтийск": ["pontic"],
    "венгерск": ["hungaric"], "австрийск": ["austriac"], "испанск": ["hispanic"], "итальянск": ["italic"],
    "французск": ["gallic"], "английск": ["anglic"], "лапландск": ["lappon"], "татарск": ["tatar", "tartar"],
    "монгольск": ["mongolic"], "маньчжурск": ["mandshur", "manshur"], "сахалинск": ["sachalin"], "байкальск": ["baicalens"],
    "азиатск": ["asiatic"], "африканск": ["african"], "аравийск": ["arabic"], "греческ": ["graec"], "турецк": ["turcic"],
    "русск": ["rossic", "ruthenic"], "волжск": ["wolgens", "volgens"], "мексиканск": ["mexican"], "перуанск": ["peruvian"],
    "бразильск": ["brasiliens"], "чилийск": ["chilens"], "капск": ["capens"], "критск": ["cretic"], "сирийск": ["syriac"],
}
_EPI_KEYS = sorted(_EPI_STEMS, key=len, reverse=True)
# Вторая часть составного видового слова («черно|краевой», «тонко|рассечённый») → корни, которые
# должны стоять в эпитете после первой части: atromarginatus, tenuisectum. Без второй части
# «Плютей чернокраевой» совпал бы с Pluteus atricapillus (это олений плютей) по одному «черн».
_COMPOUND_TAILS = {
    "цвет": ["flor", "anth"], "лист": ["fol", "phyll"], "плод": ["carp"], "колос": ["stach", "spic"],
    "кра": ["margin"], "рассечен": ["sect", "fid", "partit"], "раздельн": ["partit", "fid", "sect"],
    "надрезан": ["fid", "incis"], "стебел": ["caul"], "стебл": ["caul"], "корн": ["rhiz", "radic"],
    "корен": ["rhiz", "radic"], "голов": ["ceph", "capit"], "шляпк": ["pile", "capit"], "ножк": ["pod", "pes"],
    "пластинч": ["lamell", "phyll"], "чешуй": ["squam", "lepid"], "волос": ["pil", "trich", "crin", "chaet"],
    "шип": ["spin", "acanth"], "колюч": ["spin", "acanth"], "зубчат": ["dent", "odont", "serrat"],
    "кольц": ["annul"], "крыл": ["pter", "alat"], "семян": ["sperm"], "семен": ["sperm"], "ягод": ["bacc", "carp"],
    "чашечн": ["calyc", "sepal"], "лепестн": ["petal"], "сок": ["lact", "chyl", "succ"], "жилк": ["nerv", "ven"],
    "ветв": ["clad", "ram"], "трубчат": ["tubul", "siphon"], "звезд": ["stell", "aster"],
}
_TAIL_KEYS = sorted(set(_COMPOUND_TAILS) | set(_EPI_STEMS), key=len, reverse=True)
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ь": "", "ъ": ""})


def _norm_translit(s: str) -> str:
    return (s.replace("w", "v").replace("ii", "i").replace("y", "i").replace("tsch", "ch").replace("sch", "sh")
            .replace("ch", "kh").replace("c", "k").replace("ph", "f").replace("th", "t"))


def species_word(name: str | None) -> str | None:
    """Видовое слово имени: последнее слово до запятой, скобки или «или», если слов не меньше двух."""
    head = re.split(r",|\(|\sили\s|;", name or "")[0]
    w = re.findall(r"[а-яё-]+", head.lower().replace("ё", "е"))
    return w[-1] if len(w) >= 2 else None


def epithet_match(ru: str | None, epi: str | None) -> str | None:
    """«словарь» или «транслитерация», если русское видовое слово переводит латинский эпитет."""
    if not ru or not epi:
        return None
    for stem in _EPI_KEYS:
        if ru.startswith(stem):
            heads = [lat for lat in _EPI_STEMS[stem] if epi.startswith(lat)]
            rest = ru[len(stem):]
            # Составное слово: за основой соединительная «о» или «е» и ещё один корень
            # («черно|краевой»). «Красноватый» и «синеватый» составными не считаются.
            if rest[:1] in ("о", "е") and len(rest) >= 5 and not rest[1:].startswith("ват"):
                tail = rest[1:].lstrip("-")
                key = next((k for k in _TAIL_KEYS if tail.startswith(k)), None)
                roots = (_COMPOUND_TAILS.get(key) or _EPI_STEMS.get(key) or []) if key else []
                if any(r in epi[len(h):] for h in heads for r in roots):
                    return "словарь"
                return None
            return "словарь" if heads else None
    # Имена людей и мест пишутся в обоих языках одинаково: «Шренка» и schrenkii, «Бунге» и bungeana.
    core_ru = re.sub(r"(ский|ская|ское|ские|цкий|цкая|ный|ная|ное|ий|ая|ое|ые|ого|а|я|ы|и)$", "", ru)
    core_la = re.sub(r"(ensis|ense|iana|ianus|ianum|icus|ica|icum|ii|i|us|a|um|is|e)$", "", epi)
    if len(core_ru) < 5 or len(core_la) < 5:
        return None
    tr, la = _norm_translit(core_ru.translate(_TRANSLIT)), _norm_translit(core_la)
    if fuzz.ratio(tr, la) >= 80 or (len(la) >= 6 and (tr.startswith(la[:6]) or la.startswith(tr[:6]))):
        return "транслитерация"
    return None


def _genus_word_match(card_name: str | None, names: set[str]) -> bool:
    """Первое слово имени совпадает с первым словом народного названия рода. В длинном слове
    допускается одна буква разницы: старое написание и ошибка распознавания («Клядония»)."""
    a = _ru_words(card_name)
    if not a or len(a[0]) < 3:
        return False
    sa = _stem(a[0])
    for v in names:
        w = _ru_words(v)
        if not w:
            continue
        sb = _stem(w[0])
        if sa == sb or (min(len(sa), len(sb)) >= 6 and Levenshtein.distance(sa, sb) <= 1):
            return True
    return False


def _same_epithet(a: str | None, b: str | None) -> bool:
    """Один эпитет в разных родах грамматики: tenuisectum и tenuisecta, sibirica и sibiricum."""
    if not a or not b:
        return False
    strip = re.compile(r"(us|um|a|is|e|ii|i|ae)$")
    return strip.sub("", a.lower()) == strip.sub("", b.lower())


async def _epithet_confirms(client: httpx.AsyncClient, vern: Vernacular, r,
                            latin: str | None) -> tuple[str, str] | None:
    """Латынь переводит русское имя: видовое слово переводит эпитет, первое слово называет
    род, а GBIF знает бином точно. Возвращает (чем подтвердился эпитет, принятое имя GBIF)
    или None. Синоним, у которого принятое имя сменило и род, и эпитет, не годится: имя
    переводит чужой бином («Frangula americana» это Endotropis alnifolia, а крушиной
    американской в аптечных книгах звали каскару, Frangula purshiana)."""
    if latin_shape(latin) != "вид":
        return None
    genus, epi, _cap = latin_core(latin)
    how = epithet_match(species_word(r.name), epi)
    if not how:
        return None
    acc = await _gbif_species(client, f"{genus} {epi}", r.kingdom, exact_only=True)
    if not acc:
        return None
    a_genus, a_epi, _cap = latin_core(acc)
    if (a_genus or "").lower() != genus.lower() and not _same_epithet(a_epi, epi):
        return None
    if not _genus_word_match(r.name, await vern.names(genus)):
        return None
    return how, acc


async def run_driftreview(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    """Карточки, которые шаг drift оставил на разбор (identity.site_drift): ни прежняя латынь
    страницы, ни новая латынь карточки не подтвердились народными названиями и цитатами.
    Здесь проверяется, не переводит ли одна из них само русское имя: видовое слово
    («лекарственный», «амурский», «Воробьёва») переводит эпитет (officinalis, amurensis,
    vorobievii), первое слово имени совпадает с народным названием рода, и GBIF знает бином.
    Подтвердилась одна новая латынь — она переносится на страницу; одна прежняя —
    возвращается в карточку. Подтвердились обе или ни одна — находка остаётся на разбор."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT p.id, p.name, p.name_latin, p.kingdom, p.rank,
                   m.monograph->>'name' AS m_name, m.monograph->>'name_latin' AS m_latin
            FROM data_quality_findings f
            JOIN plants p ON p.id::text = f.entity_id
            JOIN plant_reader_monograph m ON m.plant_id = p.id
            WHERE f.check_id = :chk AND f.status = 'open'
              AND m.monograph->>'name_latin' IS DISTINCT FROM p.name_latin
            ORDER BY p.name"""), {"chk": CHECK_DRIFT})).all()
    if limit:
        rows = rows[:limit]
    c = {"step": "driftreview", "apply": apply, "cards": len(rows), "sync": 0, "revert": 0, "both": 0,
         "none": 0, "items": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        vern = Vernacular(client)
        for n, r in enumerate(rows, 1):
            ok_old = await _epithet_confirms(client, vern, r, r.m_latin)
            ok_new = await _epithet_confirms(client, vern, r, r.name_latin)
            # Прежняя подтверждена, а новая с ней один вид по GBIF (Peucedanum tenuisectum и
            # Galagania tenuisecta): возвращать нечего, на страницу идёт принятое имя из базы.
            same = None
            if ok_old and not ok_new and latin_shape(r.name_latin) == "вид":
                g, e, _cap = latin_core(r.name_latin)
                same = await _gbif_species(client, f"{g} {e}", r.kingdom, exact_only=True)
            if ok_new and (not ok_old or ok_old[1] == ok_new[1]):
                decision, latin, why = "sync", r.name_latin, f"имя карточки переводит новую латынь ({ok_new[0]})"
            elif ok_old and same and same == ok_old[1]:
                decision, latin, why = ("sync", r.name_latin,
                                        f"имя переводит прежнюю латынь ({ok_old[0]}), а по GBIF это тот же вид, что новая")
            elif ok_old and not ok_new:
                decision, latin, why = "revert", r.m_latin, f"имя карточки переводит прежнюю латынь ({ok_old[0]})"
            else:
                decision, latin, why = ("both" if ok_old else "none"), None, ""
            c[decision] += 1
            if decision in ("sync", "revert"):
                c["items"].append({"n": r.name, "o": r.m_latin, "c": r.name_latin, "d": decision, "w": why})
                if apply:
                    photo = await _drift_apply(client, r, decision, latin, why)
                    await _close_finding(r.id, "resolved", why, {"decision": decision, "latin": latin, "photo": photo},
                                         check=CHECK_DRIFT, key="driftreview")
            if progress and n % 10 == 0:
                progress({k: v for k, v in c.items() if k != "items"} | {"done": n})
    return c


async def run_driftmanual(apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    """Ручные решения по карточкам на разборе (identity.site_drift). Решение записано в
    suggested_fix находки: {"action": "manual", "decision": "sync" | "relatin", "latin": …,
    "why": …}. sync переносит латынь из базы на страницу. relatin ставит в карточку указанную
    латынь вместе с фото, семейством и современным именем нового вида. Находка закрывается с
    причиной; решения без такой записи шаг не трогает."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT p.id, p.name, p.name_latin, p.kingdom, p.rank,
                   m.monograph->>'name' AS m_name, m.monograph->>'name_latin' AS m_latin, f.suggested_fix AS fix
            FROM data_quality_findings f
            JOIN plants p ON p.id::text = f.entity_id
            JOIN plant_reader_monograph m ON m.plant_id = p.id
            WHERE f.check_id = :chk AND f.status = 'open' AND f.suggested_fix->>'action' = 'manual'
            ORDER BY p.name"""), {"chk": CHECK_DRIFT})).all()
    if limit:
        rows = rows[:limit]
    c = {"step": "driftmanual", "apply": apply, "cards": len(rows), "sync": 0, "relatin": 0, "bad": 0, "items": []}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "historical-recipes/1.0 (site identity)"}) as client:
        for n, r in enumerate(rows, 1):
            fix = r.fix or {}
            decision, why = fix.get("decision"), fix.get("why") or "ручной разбор"
            latin = r.name_latin if decision == "sync" else fix.get("latin")
            if decision not in ("sync", "relatin") or not latin:
                c["bad"] += 1
                continue
            c[decision] += 1
            item = {"n": r.name, "o": r.m_latin, "c": r.name_latin, "d": decision, "l": latin, "w": why}
            if apply:
                photo = await _drift_apply(client, r, decision, latin, why)
                await _close_finding(r.id, "resolved", why, {"decision": decision, "latin": latin, "photo": photo},
                                     check=CHECK_DRIFT, key="manual")
                item["photo"] = photo
            c["items"].append(item)
            if progress and n % 10 == 0:
                progress({k: v for k, v in c.items() if k != "items"} | {"done": n})
    return c


STEPS = {"stale": run_stale, "oldspell": run_oldspell, "genuslatin": run_genuslatin,
         "sametaxon": run_sametaxon, "junkname": run_junkname, "mismatch": run_mismatch, "drift": run_drift,
         "driftreview": run_driftreview, "driftmanual": run_driftmanual}


async def run_site_step(step: str, apply: bool, limit: int = 0, progress: Progress | None = None) -> dict:
    fn = STEPS[step]
    if step in ("genuslatin", "sametaxon", "mismatch", "drift", "driftreview", "driftmanual"):
        return await fn(apply=apply, limit=limit, progress=progress)
    return await fn(apply=apply, progress=progress)
