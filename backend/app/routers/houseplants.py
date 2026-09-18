"""Карточка ухода за комнатным растением: чтение слоя (RFC-houseplants §8).

Человек снял горшок, определитель назвал вид, и дальше ему нужен не рецепт из
травника, а ответ на вопрос «как не убить»: сколько света, как часто поливать,
какая земля, что за пятна на листьях. Слой ухода собран из книг отдельно от
гербария (`houseplant_care`), и эта ручка складывает его в карточку.

Три правила чтения, и каждое из них про честность.

Первое: показываем фразу книги, а не наш пересказ. Нормализованное значение
едет рядом для фильтров и для сравнения, но читателю достаётся то, что
написано в источнике, со ссылкой на книгу и страницу.

Второе: книги спорят, и спор виден. Если Сааков пишет одно, а Хессайон другое,
в поле стоят два голоса, а не среднее между ними.

Третье: оранжерейные советы («переваливают в парник») по умолчанию не
показываются: они написаны не для квартиры. Непроверенные имена тоже: пока
GBIF не подтвердил написание, строка может относиться к другому растению.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.houseplant_care import (
    CARE_FIELDS,
    latin_for_lookup,
    normalize_latin,
    pick_russian_name,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Порядок разделов в карточке. Сначала то, чем растение убивают чаще всего:
# свет и полив. Размножение и обрезка — в конце, это уже не спасение, а хобби.
FIELD_ORDER = ["light", "water", "temperature", "humidity", "placement", "soil",
               "feeding", "repotting", "dormancy", "pruning", "propagation"]

SEASON_RU = {"summer": "летом", "winter": "зимой"}

# Препараты в книгах датированы: список разрешённых у Воронцова идёт по письму
# Госхимкомиссии 1999 года. Часть той химии в жилом помещении сегодня неуместна.
CHEMICALS_NOTICE = ("Названия препаратов приведены по книге. Список разрешённых "
                    "средств с тех пор менялся: сверьтесь с современными правилами "
                    "перед обработкой, особенно в жилой комнате.")


def _rows_to_fields(rows, glossary: dict[tuple[str, str], dict]) -> list[dict]:
    """Складывает строки базы в разделы карточки, сохраняя расхождения книг.

    Ссылки на вводную часть книги разворачиваются здесь же: «Уход общий» сам по
    себе читателю ничего не говорит, а рядом с расшифровкой из той же книги
    становится советом.
    """
    # Одна и та же фраза книги бывает записана дважды: под родом и под его
    # видом. Для читателя это один голос, поэтому повтор цитаты убираем, а
    # оставляем ту запись, что говорит о более узком таксоне.
    grouped: dict[tuple[str, str | None], list] = {}
    seen: dict[tuple, object] = {}
    for row in rows:
        key = (row.field, row.season, row.source_id, row.quote)
        previous = seen.get(key)
        if previous is not None:
            if len(row.taxon_latin) > len(previous.taxon_latin):
                bucket = grouped[(row.field, row.season)]
                bucket[bucket.index(previous)] = row
                seen[key] = row
            continue
        seen[key] = row
        grouped.setdefault((row.field, row.season), []).append(row)

    fields: list[dict] = []
    for field_name in FIELD_ORDER:
        for season in (None, "summer", "winter"):
            voices = grouped.get((field_name, season))
            if not voices:
                continue
            title = CARE_FIELDS.get(field_name, field_name)
            if season:
                title = f"{title}, {SEASON_RU[season]}"
            fields.append({
                "field": field_name,
                "season": season,
                "title": title,
                # Значение берём у первого голоса: оно одинаковое по смыслу,
                # а расходятся книги словами, и слова показываем все.
                "value": voices[0].value,
                "voices": [{
                    "text": v.value_text,
                    "quote": v.quote,
                    "source": v.source_id,
                    "book": v.book,
                    "year": v.year,
                    "page": v.page,
                    "reference": v.reference,
                    "reference_text": (glossary.get((v.source_id, v.reference)) or {}).get("body"),
                    # Чей это голос: рода или конкретного вида. Читателю важно
                    # знать, что совет написан про хойю мясистую, а не про род.
                    "about": v.taxon_latin,
                } for v in voices],
            })
    return fields


@router.get("/{latin}/care")
async def get_care(
    latin: str,
    include_greenhouse: bool = Query(False, description="показать и оранжерейные советы"),
    db: AsyncSession = Depends(get_db),
):
    """Карточка ухода по латинскому имени вида или рода.

    Спрашивают чаще о виде («Hoya carnosa»), а книги пишут о роде («Hoya»), и
    для ухода это правильно: сорта одного рода живут одинаково. Поэтому если по
    виду записей нет, карточка собирается по его роду и честно говорит об этом.
    """
    asked = normalize_latin(latin)
    lookup = latin_for_lookup(asked)
    genus = lookup.split(" ")[0] if lookup else ""

    condition = "" if include_greenhouse else " AND c.greenhouse = false"
    # Книга описывает уход то у рода, то у отдельного вида, и при разборе эти
    # утверждения ложатся в разные строки: у трети родов в отдельной строке
    # осталось одно-два утверждения. Для читателя это один и тот же вопрос,
    # поэтому карточка собирает род вместе с его видами, помечая, где чьё.
    query = text(f"""
        SELECT c.field, c.season, c.value, c.value_text, c.quote, c.page,
               c.source_id, c.taxon_ru, c.taxon_latin, c.reference,
               s.title AS book, s.year AS year
        FROM houseplant_care c
        JOIN houseplant_source s ON s.id = c.source_id
        WHERE (c.taxon_latin = :name OR c.taxon_latin LIKE :prefix)
          AND c.latin_verified = true {condition}
        ORDER BY c.field, c.season NULLS FIRST, s.year DESC NULLS LAST
    """)

    # Спросили вид — берём его строки и строки его рода; спросили род — его
    # строки и строки всех его видов.
    if " " in lookup:
        rows = (await db.execute(query, {"name": lookup, "prefix": lookup})).all()
        used, scope = lookup, "species"
        if not rows and genus:
            rows = (await db.execute(query, {"name": genus, "prefix": f"{genus} %"})).all()
            used, scope = genus, "genus"
        else:
            extra = (await db.execute(query, {"name": genus, "prefix": genus})).all()
            seen = {(r.field, r.season, r.quote) for r in rows}
            rows = list(rows) + [r for r in extra if (r.field, r.season, r.quote) not in seen]
    else:
        rows = (await db.execute(query, {"name": lookup, "prefix": f"{lookup} %"})).all()
        used, scope = lookup, "genus"

    if not rows:
        return {"latin": asked, "found": False, "fields": [], "problems": []}

    problems = (await db.execute(text("""
        SELECT p.kind, p.name, p.symptom, p.cause, p.remedy, p.chemicals,
               p.quote, p.page, p.source_id, s.title AS book
        FROM houseplant_problem p
        JOIN houseplant_source s ON s.id = p.source_id
        WHERE p.taxon_latin = :name
        ORDER BY p.kind, p.name
    """), {"name": used})).all()

    # Словарь ссылок нужен только тем книгам, которые в этой карточке говорят.
    glossary: dict[tuple[str, str], dict] = {}
    refs = {(r.source_id, r.reference) for r in rows if r.reference}
    if refs:
        found = (await db.execute(text('''
            SELECT source_id, key, body, page FROM houseplant_glossary
            WHERE (source_id, key) IN (
                SELECT unnest(cast(:sources AS text[])), unnest(cast(:keys AS text[]))
            )
        '''), {"sources": [s for s, _ in refs], "keys": [k for _, k in refs]})).all()
        glossary = {(g.source_id, g.key): {"body": g.body, "page": g.page} for g in found}

    fields = _rows_to_fields(rows, glossary)
    return {
        "latin": used,
        "asked": asked,
        "scope": scope,                       # species | genus
        "name_ru": pick_russian_name([r.taxon_ru for r in rows]),
        "found": True,
        "fields": fields,
        "sources": sorted({r.book for r in rows}),
        "problems": [{
            "kind": p.kind,
            "name": p.name,
            "symptom": p.symptom,
            "cause": p.cause,
            "remedy": p.remedy,
            "chemicals": p.chemicals or [],
            "source": p.source_id,
            "book": p.book,
            "page": p.page,
        } for p in problems],
        "chemicals_notice": CHEMICALS_NOTICE if any(p.chemicals for p in problems) else None,
    }


@router.get("/")
async def list_houseplants(db: AsyncSession = Depends(get_db)):
    """Какие растения слой уже знает: имя, число утверждений, книги."""
    rows = (await db.execute(text("""
        SELECT taxon_latin, max(taxon_ru) AS name_ru, count(*) AS facts,
               count(DISTINCT source_id) AS sources
        FROM houseplant_care
        WHERE latin_verified = true
        GROUP BY taxon_latin
        ORDER BY facts DESC
    """))).all()
    return {
        "count": len(rows),
        "items": [{"latin": r.taxon_latin, "name_ru": r.name_ru,
                   "facts": r.facts, "sources": r.sources} for r in rows],
    }
