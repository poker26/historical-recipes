"""Идентичность карточек лечебника XV века «Ненужное для неучей» (Амирдовлат Амасиаци).

Имена карточек из этой книги это транслитерация армянских, арабских, персидских
и греческих названий («шапугай», «Лавз эл Булв»). Ни один добор латыни их не
берёт. Зато издание 1990 года несёт научный аппарат: у каждой статьи номер
(«§ 185.»), а в конце книги указатель латинских названий с номерами статей
(«Peganum harmala L. 612, 1143, 1284»). Этот указатель и есть источник
идентификации, причём авторитетный: его составил редактор издания.

Как идём:

1. Из страниц указателя собираем карту «номер статьи → латинские имена».
2. У каждой карточки без латыни читаем номера статей из текста её упоминаний.
3. Если по всем статьям карточки указатель даёт одно имя, оно и берётся.
   Если несколько, выбираем то, чьё написание проступает в самом тексте
   статьи сквозь искажения распознавания (кириллица вместо латиницы), а если
   и это не помогает, модель выбирает из списка кандидатов, и только из него.
4. GBIF проверяет имя и говорит, растение это, гриб, животное или вообще не
   организм (камни, смолы, воды): животные и вещества уходят из гербария
   сменой ``kingdom``.
5. Растение: латынь пишется в карточку; если карточка с такой латынью уже
   есть, статья лечебника сливается в неё, а её транслитерированное имя
   остаётся историческим названием вида. Так средневековые применения
   попадают к настоящим растениям, как и задумано для «Имён» на сайте.

Всё пишется в ``card_identity_audit``; отметки в ``data_quality_findings``
(``identity.amirdovlat``) делают шаг идемпотентным.
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
from sqlalchemy import text

from app.database import async_session
from app.services.identity_cleanup import (
    AMIRDOVLAT, _audit, _finding, _purge_qdrant, _relatin, binomial_core, gbif_match, merge_card,
)
from app.services.llm import chat_completion_json
from app.services.plant_matching import _latin_key

logger = logging.getLogger(__name__)

Progress = Callable[[dict], None]
CHECK = "identity.amirdovlat"
GBIF_PACE = 0.4

# Номер СВОЕЙ статьи стоит в начале текста: «§ 185. 1. …». Номера внутри квадратных
# скобок это перекрёстные ссылки на другие статьи и источники («[43, § 629; 28, § 286]»),
# брать их нельзя: по ним карточка уезжала к чужому растению.
ENTRY_RE = re.compile(r"[§$&]\s*(\d{1,4})\s*\.")
CROSSREF_RE = re.compile(r"\[[^\]]*\]")


def own_text(t: str) -> str:
    """Текст статьи без перекрёстных ссылок в квадратных скобках."""
    return CROSSREF_RE.sub(" ", t or "")
INDEX_PAIR_RE = re.compile(
    r"([A-Z][a-z]{2,}(?: [a-z][a-z\-]{2,})?(?: (?:L\.|var\.|subsp\.|[A-Z][a-zA-Z\.]+))*)\s+((?:\d{1,4},?\s*)+)")
LATIN_DENSITY_RE = re.compile(r"\b[A-Z][a-z]{3,} [a-z]{4,}\b")
# Кириллические двойники латинских букв, какими их видит распознавание.
LOOKALIKE = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
    "У": "Y", "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "і": "i", "Ь": "b", "ш": "w",
    "З": "3", "з": "3", "Г": "L", "г": "r", "п": "n", "и": "u", "й": "u", "т": "t", "л": "l", "б": "b", "в": "b",
    "д": "d", "к": "k", "м": "m", "н": "h", "ф": "f", "ц": "c", "ч": "y",
})

CHOOSE_SYS = (
    "Ты историк фармакогнозии. Дана статья средневекового лечебника (русский перевод с комментарием) и список "
    "латинских названий из научного указателя к этой статье. Выбери то ОДНО название, о котором статья говорит "
    "как об основном предмете; остальные в списке это сравнения и синонимы. Если статья явно не о растении или "
    "выбрать нельзя, верни NONE. Строго JSON: {\"latin\":\"<из списка>\"|\"NONE\",\"confidence\":0-100}"
)


async def _book_id(db) -> uuid.UUID | None:
    return (await db.execute(text("SELECT id FROM books WHERE title = :t LIMIT 1"), {"t": AMIRDOVLAT})).scalar()


async def build_index(db, book_id) -> dict[int, set[str]]:
    """Страницы указателя находим по плотности латинских биномов, чтобы не зависеть
    от нумерации конкретного скана."""
    pages = (await db.execute(text(
        "SELECT page_number, raw_text FROM book_pages WHERE book_id = :b AND raw_text IS NOT NULL ORDER BY page_number"),
        {"b": book_id})).all()
    dense = [(p, t) for p, t in pages if len(LATIN_DENSITY_RE.findall(t or "")) >= 25]
    idx: dict[int, set[str]] = defaultdict(set)
    for _, t in dense:
        flat = re.sub(r"\s+", " ", t or "")
        for lat, nums in INDEX_PAIR_RE.findall(flat):
            for n in re.findall(r"\d+", nums):
                idx[int(n)].add(lat.strip())
    logger.info("amirdovlat index: %d dense pages, %d entries", len(dense), len(idx))
    return idx


def disambiguate_by_text(cands: set[str], texts: list[str]) -> str | None:
    body = " ".join(own_text(t) for t in texts).translate(LOOKALIKE)
    scored = sorted(((fuzz.partial_ratio(l.split(" L.")[0][:30], body), l) for l in cands), reverse=True)
    if scored and scored[0][0] >= 78 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 8):
        return scored[0][1]
    return None


_AUTHOR_RE = re.compile(r"\s+(?:L\.|var\.|subsp\.|[A-Z][a-zA-Z\-]*\.|[A-Z][a-z]+ ex [A-Z][a-z]+\.?|\([^)]*\)).*$")


def name_core(chosen: str) -> str:
    """Имя без автора: «Rosa Tourn.» → «Rosa», «Aloexylon agallochum Lour.» → «Aloexylon agallochum»."""
    core = binomial_core(chosen)
    if core:
        return core
    return _AUTHOR_RE.sub("", chosen.strip()).strip() or chosen.strip()


def has_author(chosen: str) -> bool:
    return bool(_AUTHOR_RE.search(chosen.strip()))


# Головные слова латинских названий веществ, продуктов и частей в указателе: они
# выглядят как биномы («Lapis armeniacus», «Succus uvarum»), но организмом не являются.
_SUBSTANCE_HEADS = {
    "lapis", "succus", "semen", "caro", "gummi", "pulmentum", "digiti", "uvae", "aqua", "oleum", "resina", "pix",
    "sal", "terra", "ferrum", "plumbum", "cuprum", "aes", "vinum", "mel", "butyrum", "lac", "ungula", "ebur",
    "cornu", "os", "sanguis", "fel", "adeps", "urina", "stercus", "cinis", "calx", "sulphur", "alumen", "nitrum",
    "bitumen", "naphta", "petroleum", "ambra", "moschus", "castoreum", "bezoar", "cerussa", "cadmia", "cadmie",
    "gypsum", "argilla", "bolus", "spuma", "zyhum", "gluten", "gallae", "omphacium", "syricon", "indicum",
    "hydrargyrum", "chrysocolla", "borax", "bdellium", "manna", "acetum", "farina", "panis", "cera", "sapo",
    "vitrum", "arsenicum", "auripigmentum", "stibium", "cinnabaris", "magnes", "smaragdus", "margarita",
    "corallium", "stannum", "aurum", "argentum", "orichalcum", "chalcitis", "misy", "sory", "atramentum",
    "fuligo", "pulvis", "cortex", "lignum", "radix", "folia", "flores", "fructus", "amylum", "saccharum",
    "theriaca", "opium", "camphora",
}
_ANIMAL_HEADS = {"coccus", "leo", "pediculus", "lumbricus", "cancer", "scorpio", "vipera", "asinus", "equus", "bos",
                 "canis", "felis", "lepus", "ursus", "columba", "gallus", "anser", "milvus", "perdix", "pavo",
                 "phoenicopterus", "lacerta", "testudo", "sepia", "purpura", "conchylia", "oniscus", "tegula",
                 "omphax", "trachea", "ren", "pelles", "phalangium"}


def _kingdom_of(g: dict | None, chosen: str) -> str | None:
    """Что говорит GBIF о найденном имени. ``None`` означает «решить нельзя»
    (временный отказ справочника или имя, которого он не знает, но которое
    записано в указателе как имя организма с автором: старая латынь)."""
    head = (chosen.strip().split() or [""])[0].lower()
    if head in _SUBSTANCE_HEADS:
        return "вещество"
    if head in _ANIMAL_HEADS:
        return "животное"
    if g is None:
        return None
    if g.get("matchType") in (None, "NONE"):
        # GBIF не знает имени. Имя с автором или биномом это устаревшая латынь
        # растения из указателя, а одно слово без автора («Ferrum», «Bezoar») это
        # вещество или предмет.
        return None if (has_author(chosen) or binomial_core(chosen)) else "вещество"
    k = g.get("kingdom") or ""
    if k in ("Plantae", "Fungi", "Chromista"):
        return "гриб" if k == "Fungi" else "растение"
    if k == "Animalia":
        return "животное"
    return "вещество"


async def run_amirdovlat(apply: bool, limit: int = 0, use_llm: bool = True,
                         progress: Progress | None = None) -> dict:
    c: dict = {"seen": 0, "unique": 0, "by_text": 0, "by_llm": 0, "merged": 0, "relatin": 0,
               "animal": 0, "substance": 0, "ambiguous": 0, "no_entry": 0, "not_in_index": 0, "errors": 0}
    samples: dict[str, list] = {}

    def note(k: str, item) -> None:
        samples.setdefault(k, [])
        if len(samples[k]) < 8:
            samples[k].append(item)

    async with async_session() as db:
        bid = await _book_id(db)
        if bid is None:
            return {"step": "amirdovlat", "error": "book not found"}
        idx = await build_index(db, bid)
        rows = (await db.execute(text("""
            SELECT p.id, p.name, p.name_latin, p.kingdom,
                   array_agg(m.original_text) AS texts
            FROM plants p JOIN plant_book_mentions m ON m.plant_id = p.id
            WHERE m.book_id = :b AND p.kingdom IN ('растение', 'гриб')
              AND NOT EXISTS (SELECT 1 FROM data_quality_findings f WHERE f.check_id = :chk AND f.entity_id = p.id::text)
            GROUP BY p.id, p.name, p.name_latin, p.kingdom ORDER BY p.name"""), {"b": bid, "chk": CHECK})).all()
    rows = [r for r in rows if not binomial_core(r.name_latin)]
    if limit:
        rows = rows[:limit]

    dead: list[str] = []
    async with httpx.AsyncClient(timeout=25, limits=httpx.Limits(max_keepalive_connections=0),
                                 headers={"User-Agent": "historical-recipes/1.0 (identity amirdovlat)"}) as client:
        for pid, name, latin, kingdom, texts in rows:
            c["seen"] += 1
            texts = [t for t in (texts or []) if t]
            entries = {int(n) for t in texts for n in ENTRY_RE.findall(own_text(t))}
            ev: dict = {"name": name, "entries": sorted(entries)}
            try:
                if not entries:
                    c["no_entry"] += 1
                    if apply:
                        await _finding(pid, CHECK, "dismissed", f"{name}: номер статьи не найден", ev, "none")
                    note("no_entry", name)
                    continue
                cands: set[str] = set()
                for e in entries:
                    cands |= idx.get(e, set())
                if not cands:
                    c["not_in_index"] += 1
                    if apply:
                        await _finding(pid, CHECK, "dismissed", f"{name}: статьи нет в указателе", ev, "none")
                    note("not_in_index", (name, sorted(entries)))
                    continue
                chosen = None
                how = None
                if len(cands) == 1:
                    chosen, how = next(iter(cands)), "unique"
                else:
                    chosen = disambiguate_by_text(cands, texts)
                    how = "by_text" if chosen else None
                    if not chosen and use_llm:
                        try:
                            ans = await chat_completion_json(
                                [{"role": "system", "content": CHOOSE_SYS},
                                 {"role": "user", "content": json.dumps(
                                     {"entry": " ".join(texts)[:1800], "candidates": sorted(cands)}, ensure_ascii=False)}],
                                task="plant_extraction", temperature=0.0, max_tokens=120)
                            pick = (ans.get("latin") if isinstance(ans, dict) else None) or "NONE"
                            conf = int((ans.get("confidence") if isinstance(ans, dict) else 0) or 0)
                            if pick in cands and conf >= 70:
                                chosen, how = pick, "by_llm"
                            ev["llm"] = {"pick": pick, "conf": conf}
                        except Exception:  # noqa: BLE001
                            c["errors"] += 1
                if not chosen:
                    c["ambiguous"] += 1
                    ev["candidates"] = sorted(cands)
                    if apply:
                        await _finding(pid, CHECK, "open", f"{name}: несколько кандидатов в указателе", ev, "review")
                    note("ambiguous", (name, sorted(cands)[:3]))
                    continue
                c[how] += 1
                ev["chosen"] = chosen
                core = name_core(chosen)
                g = await gbif_match(client, core, None)
                await asyncio.sleep(GBIF_PACE)
                kind = _kingdom_of(g, chosen)
                ev["gbif"] = {"matchType": (g or {}).get("matchType"), "kingdom": (g or {}).get("kingdom"),
                              "name": (g or {}).get("canonicalName")}
                if kind is None:
                    if g is None:
                        # Временный отказ GBIF: отметку не ставим, карточка вернётся в следующий прогон.
                        c["errors"] += 1
                        continue
                    # Старая латынь, которой GBIF не знает: пишем как есть, царство не меняем.
                    kind = kingdom or "растение"
                    ev["gbif_unknown_name"] = True
                if kind in ("животное", "вещество"):
                    c["animal" if kind == "животное" else "substance"] += 1
                    if apply:
                        async with async_session() as db:
                            await _audit(db, "amirdovlat", "kingdom", pid, name, latin,
                                         extra={"kind": kind, "latin": chosen, **ev})
                            await db.execute(text("UPDATE plants SET kingdom = :k, name_latin = COALESCE(name_latin, :l) WHERE id = :id"),
                                             {"k": kind, "l": core, "id": pid})
                            await db.commit()
                        await _finding(pid, CHECK, "resolved", f"{name}: {kind} ({core})", ev, "kingdom")
                    note(kind, (name, core))
                    continue
                accepted = ((g or {}).get("canonicalName") if (g or {}).get("matchType") not in (None, "NONE") else None) or core
                new_latin = binomial_core(accepted) or accepted
                if apply:
                    async with async_session() as db:
                        await _relatin(db, pid, name, latin or "", new_latin, "amirdovlat", "relatin", ev)
                        # Имя карточки это транслитерация; в заголовок ставим латынь, транслитерацию
                        # сохраняем историческим названием (позже iNat даст русское имя).
                        await db.execute(text("""
                            UPDATE plants SET
                              names_historical = CASE WHEN :n = ANY(COALESCE(names_historical, ARRAY[]::text[])) THEN names_historical
                                                      ELSE array_append(COALESCE(names_historical, ARRAY[]::text[]), :n) END,
                              name = :l, kingdom = :k
                            WHERE id = :id"""), {"n": name, "l": new_latin, "k": kind, "id": pid})
                        key = _latin_key(new_latin)
                        twin = None
                        if key:
                            others = (await db.execute(text(
                                "SELECT id, name, name_latin FROM plants WHERE id <> :id AND lower(name_latin) LIKE :g"),
                                {"id": pid, "g": key.split()[0] + " %"})).all()
                            twins = [o for o in others if _latin_key(o.name_latin) == key]
                            twin = max(twins, key=lambda o: (o.name != new_latin, len(o.name or ""))) if twins else None
                        if twin is not None:
                            await merge_card(db, pid, twin.id, "amirdovlat", f"{how}: {name} → {twin.name} ({new_latin})")
                            c["merged"] += 1
                            ev["merged_into"] = twin.name
                        else:
                            c["relatin"] += 1
                        await db.commit()
                    if twin is not None:
                        dead.append(str(pid))
                    await _finding(pid, CHECK, "resolved", f"{name} → {new_latin}" + (f" (в «{twin.name}»)" if twin else ""), ev, how)
                else:
                    c["relatin"] += 1
                note(how, (name, new_latin))
            except Exception as e:  # noqa: BLE001 — одна карточка не должна ронять прогон
                logger.warning("amirdovlat failed for %s: %s", name, e)
                c["errors"] += 1
                continue
            if progress:
                progress({"step": "amirdovlat", **c})
            if len(dead) >= 200:
                await _purge_qdrant(dead)
                dead = []
    if dead:
        await _purge_qdrant(dead)
    return {"step": "amirdovlat", "apply": apply, **c, "samples": samples}
