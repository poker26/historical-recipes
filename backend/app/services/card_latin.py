"""Латынь карточки без фамилии автора.

Зачем модуль. Латинское имя стоит в карточке сразу под русским, и человек его
читает. Сейчас у 196 карточек там вместе с именем растения напечатана фамилия
ботаника, который это имя описал: «Ficus elastica Roxb.», «Lactarius resimus
(Fr.) Fr.», «Leucocybe connata (Schumach.) Vizzini, P.Alvarado, G.Moreno &
Consiglio». Для справочника это норма, для карточки в телефоне это мусор: за
две недели такие карточки показались 349 раз.

Чем эта чистка отличается от подготовки имени к сверке. В слое комнатных есть
``latin_for_lookup``: он режет всё сомнительное до рода, потому что роду
справочник всё равно ответит. Здесь так нельзя. «Acer Negundo L.» — это клён
ясенелистный, и обрезка до «Acer» превратила бы карточку вида в карточку рода,
то есть потеряла бы то, ради чего человек её открыл.

Поэтому решение принимает не правило, а GBIF. Модуль строит кандидата и
спрашивает справочник: если тот знает имя и подтверждает его, кандидат
принимается; если молчит или не знает, карточка остаётся как была. Молчание
сети ничего не портит.
"""

from __future__ import annotations

import re

# Хвост в скобках — это всегда автор, в имени растения скобок не бывает:
# «Lactarius resimus (Fr.) Fr.», «Citrus maxima (Burm.) Merr.».
_PARENS_RE = re.compile(r"\([^)]*\)")

# Перечисление авторов через запятую и амперсанд: «Vizzini, P.Alvarado, G.Moreno
# & Consiglio». Всё, что после первой запятой, к имени растения не относится.
_AUTHOR_LIST_RE = re.compile(r"\s*[,&].*$")

# Сорт в кавычках: «Ctenanthe oppenheimiana 'variegata'».
_CULTIVAR_RE = re.compile(r"""['"“”‘’][^'"“”‘’]*['"“”‘’]""")

# Служебные слова, после которых начинается уточнение ранга, а не имя вида.
_RANK_WORDS = {"var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f", "f.",
               "forma", "cv", "cv.", "sect", "sect."}


def looks_like_author(word: str) -> bool:
    """Слово — фамилия ботаника, а не эпитет вида.

    Эпитет вида всегда со строчной буквы и без точки: ``elastica``, ``resimus``.
    Фамилия приходит либо с точкой сокращения («Roxb.», «L.», «Mill.»), либо
    целиком с заглавной («Moench», «Schott»). Отдельно ловим одиночные буквы:
    «L» без точки — это всё ещё Линней.
    """
    if not word:
        return False
    if word.endswith("."):
        return True
    if len(word) <= 2:
        return True
    return word[:1].isupper()


def candidate_latin(name: str) -> str:
    """Имя растения без фамилии автора, сорта и уточнения ранга.

    Возвращает то, что стоит спросить у GBIF. Пустая строка означает, что чинить
    нечего: имя либо уже чистое, либо разобрать его правилом не вышло.
    """
    body = _CULTIVAR_RE.sub(" ", name or "")
    body = _PARENS_RE.sub(" ", body)
    body = _AUTHOR_LIST_RE.sub("", body)
    words = [w for w in body.replace(" ", " ").split() if w]
    if not words:
        return ""

    genus = words[0]
    # Книга иногда печатает имя целиком заглавными: «CAPSICUM ANNUUM L.».
    if genus.isupper() and len(genus) > 2:
        genus = genus.capitalize()
    if not genus[:1].isalpha():
        return ""

    if len(words) == 1:
        return genus

    second = words[1]
    if second.lower().rstrip(".") in {w.rstrip(".") for w in _RANK_WORDS}:
        return genus

    # Эпитет вида со строчной буквы и без точки. Заглавная встречается в старых
    # книгах («Acer Negundo L.»), и это всё-таки эпитет, а не автор, — но решать
    # будет справочник, наше дело предложить.
    epithet = second.rstrip(",")
    if epithet.isupper():
        epithet = epithet.lower()
    if looks_like_author(epithet) and not epithet[:1].isupper():
        return genus
    if epithet.endswith(".") or len(epithet) < 3:
        return genus

    return f"{genus} {epithet[:1].lower() + epithet[1:]}"


def has_author_tail(name: str) -> bool:
    """В имени есть то, что человеку в карточке читать незачем."""
    body = (name or "").strip()
    if not body:
        return False
    if _PARENS_RE.search(body) or "&" in body or "," in body:
        return True
    words = body.split()
    if len(words) > 2:
        return True
    if len(words) == 2 and looks_like_author(words[1]):
        return True
    return any(ch.isupper() for ch in body[1:]) and body.isupper()


async def clean_card_latins(db, limit: int = 0, apply: bool = False,
                            on_piece=None) -> dict:
    """Снимает фамилии авторов с латинских имён карточек.

    Меняем имя только тогда, когда справочник подтвердил ровно то, что мы
    предложили. Если GBIF отвечает другим именем — например, переносит вид в
    другой род, — карточка остаётся как была: это уже не чистка подписи, а смена
    тождества, и решать её надо отдельно, по одной карточке.

    Столкновения тоже не трогаем. Когда чистое имя уже занято другой карточкой,
    переименование сложило бы две записи в одну латынь и завело бы дубль вместо
    порядка. Такие случаи возвращаются списком.
    """
    from sqlalchemy import text

    from app.services.houseplant_care import resolve_latin

    rows = (await db.execute(text(
        "SELECT id, name, name_latin FROM plants "
        "WHERE name_latin IS NOT NULL AND name_latin <> '' ORDER BY name_latin"
    ))).all()

    dirty = [r for r in rows if has_author_tail(r.name_latin)]
    if limit:
        dirty = dirty[:limit]

    taken = {r.name_latin.strip().lower(): r.id for r in rows}

    out = {"looked_at": len(dirty), "cleaned": 0, "collisions": [],
           "gbif_says_other": [], "unknown": 0}

    for number, row in enumerate(dirty, start=1):
        candidate = candidate_latin(row.name_latin)
        if not candidate or candidate.lower() == row.name_latin.strip().lower():
            continue

        accepted, verified = await resolve_latin(candidate)
        if not verified:
            out["unknown"] += 1
        elif accepted.strip().lower() != candidate.strip().lower():
            out["gbif_says_other"].append(
                {"was": row.name_latin, "asked": candidate, "gbif": accepted})
        elif taken.get(candidate.lower()) not in (None, row.id):
            out["collisions"].append({"was": row.name_latin, "clean": candidate})
        else:
            if apply:
                await db.execute(text(
                    "UPDATE plants SET name_latin = :latin WHERE id = :id"),
                    {"latin": candidate, "id": row.id})
                await db.commit()
            taken[candidate.lower()] = row.id
            out["cleaned"] += 1

        if on_piece:
            await on_piece({"done": number, "total": len(dirty),
                            "was": row.name_latin, "cleaned": out["cleaned"]})

    return out
