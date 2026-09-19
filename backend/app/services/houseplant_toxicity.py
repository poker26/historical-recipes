"""Опасность комнатных растений: разбор пособия по ядовитым комнатным.

Слой ухода отвечает, как растение не убить. Этот отвечает на обратный вопрос:
чем оно опасно для человека, ребёнка и кошки. Источник отдельный и по правилу
источников: книга по уходу не становится источником по токсикологии оттого, что
упомянула жгучий сок.

Пособие Морозовой и Вандышева устроено удобно: глава, номер, заголовок вида с
латынью и семейством, дальше разделы — ботаническое описание, действующие
вещества, картина отравления, первая помощь, использование. Нарезка идёт по
заголовку статьи, потому что первая помощь стоит в конце и разрез посреди
страницы оторвал бы её от растения.

Правило то же, что во всём слое: утверждение без дословной цитаты из скана
выбрасывается. Совет «промыть желудок» человек выполняет буквально.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.houseplant_care import normalize_text, quote_is_grounded
from app.services.llm import chat_completion_json

# Заголовок статьи: «1.1. Агава американская — Agave americana L.»
# Ловим по номеру пункта и русскому имени, а латынь в шаблон НЕ закладываем:
# распознаватель переводит её в кириллицу («Адауе атепсапа» вместо «Agave
# americana»), и требование латинских букв не находило вообще ничего.
_ARTICLE_HEADER_RE = re.compile(
    r"^\s*(\d{1,2}\.\d{1,2})\.?\s+([А-ЯЁ][А-Яа-яЁё\s\-]{2,60}?)\s*[—–-]\s*(\S[^\n]*)$",
    re.M,
)

# Насколько всё серьёзно. Словами книги: от «раздражает кожу» до «смертельно».
SEVERITY = {
    "irritant": "раздражает кожу и слизистые",
    "toxic": "ядовито при попадании внутрь",
    "dangerous": "опасно для жизни",
}

# Строка оглавления: отточие и номер страницы в конце.
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d{1,3}\s*$|…\s*\d{1,3}\s*$", re.M)

# Настоящая статья о растении заведомо длиннее короткой строки оглавления.
MIN_ARTICLE_CHARS = 600

_DANGEROUS_RE = re.compile(r"смертельн|летальн|остановк\w+ сердц|угроза жизни|"
                           r"тяжёл\w+ отравлени|тяжел\w+ отравлени", re.I)
_TOXIC_RE = re.compile(r"ядовит|отравлени|токсичн|рвот|судорог|тошнот", re.I)
_IRRITANT_RE = re.compile(r"раздраж|ожог|жжени|дерматит|волдыр", re.I)


@dataclass
class ToxicityFact:
    """Что книга говорит об опасности одного растения."""

    taxon_latin: str = ""
    taxon_ru: str = ""
    toxin: str = ""
    parts: list[str] = field(default_factory=list)
    symptoms: str = ""
    first_aid: str = ""
    children: str = ""
    pets: str = ""
    severity: str = ""
    quote: str = ""
    page: int | None = None


def grade_severity(*texts: str) -> str:
    """Оценивает тяжесть по словам книги, не додумывая за неё.

    Порядок проверки идёт от худшего: фраза про смертельный исход важнее
    упоминания рвоты, а рвота важнее покраснения кожи.
    """
    body = " ".join(t or "" for t in texts)
    if _DANGEROUS_RE.search(body):
        return "dangerous"
    if _TOXIC_RE.search(body):
        return "toxic"
    if _IRRITANT_RE.search(body):
        return "irritant"
    return ""


def find_articles(pages: list[str]) -> list[tuple[str, str, str, int]]:
    """Режет книгу на статьи о растениях.

    Возвращает ``(латынь, русское имя, текст статьи, страница)``. Резать по
    заголовкам обязательно: первая помощь стоит в конце статьи, и разрез по
    странице оторвал бы её от растения, к которому она относится.
    """
    offsets: list[int] = []
    cursor = 0
    for page_text in pages:
        offsets.append(cursor)
        cursor += len(page_text) + 1
    whole = "\n".join(pages)

    def page_of(position: int) -> int:
        low, high = 0, len(offsets) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if offsets[middle] <= position:
                low = middle
            else:
                high = middle - 1
        return low + 1

    matches = list(_ARTICLE_HEADER_RE.finditer(whole))
    out: list[tuple[str, str, str, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(whole)
        body = whole[match.start():end]

        # Оглавление в конце книги повторяет все заголовки («1.19. Диффенбахия
        # пятнистая ...... 56»), и без этой проверки каждая статья находилась
        # дважды: настоящая и пустая строка из оглавления.
        if _TOC_LINE_RE.search(match.group(0)) or len(body.strip()) < MIN_ARTICLE_CHARS:
            continue

        russian = match.group(2).strip()
        tail = match.group(3).strip()
        # Латынь из заголовка берём, только если распознаватель оставил её
        # латиницей. Иначе имя добудем по русскому названию через iNaturalist.
        latin = ""
        if re.match(r"^[A-Z][A-Za-z\-]{2,}", tail):
            latin = " ".join(tail.split()[:2]).rstrip(".,")
        out.append((latin, russian, body.strip(), page_of(match.start())))
    return out


_PROMPT = """Ты разбираешь статью из пособия по ядовитым комнатным растениям.

Верни СТРОГО JSON:
{
  "latin": "<латинское имя вида из заголовка>",
  "ru": "<русское имя из заголовка>",
  "toxin": "<группа веществ, которыми растение опасно, словами книги>",
  "parts": ["<ядовитые части: сок, листья, семена, корень…>"],
  "symptoms": "<картина отравления: что происходит с человеком>",
  "first_aid": "<первая помощь, как её описывает книга>",
  "children": "<чем опасно для детей, если книга об этом говорит>",
  "pets": "<чем опасно для животных, если книга об этом говорит>",
  "quote": "<дословный кусок статьи про опасность, слово в слово>"
}

Правила:
1. Цитата берётся из текста БЕЗ изменений и должна говорить именно об опасности.
2. Ничего не додумывай: чего книга не сказала, оставь пустой строкой или пустым
   списком. Пустое поле честнее выдуманного.
3. Ботаническое описание, историю и применение в медицине НЕ пересказывай.
4. Дозировки и препараты переписывай точно, как в книге.

Текст статьи:
---
{article}
---"""


async def extract_toxicity(article: str, page: int | None = None) -> ToxicityFact | None:
    """Разбирает одну статью пособия. Без дословной цитаты факт не живёт."""
    source_text = normalize_text(article)
    data = await chat_completion_json(
        [{"role": "user", "content": _PROMPT.replace("{article}", article)}],
        task="plant_extraction", temperature=0.1,
    )
    if not isinstance(data, dict):
        return None

    quote = (data.get("quote") or "").strip()
    if not quote_is_grounded(quote, source_text):
        return None

    symptoms = normalize_text(data.get("symptoms") or "")
    first_aid = normalize_text(data.get("first_aid") or "")
    toxin = normalize_text(data.get("toxin") or "")
    parts = [normalize_text(p) for p in (data.get("parts") or []) if p]

    return ToxicityFact(
        taxon_latin=normalize_text(data.get("latin") or ""),
        taxon_ru=normalize_text(data.get("ru") or ""),
        toxin=toxin,
        parts=parts,
        symptoms=symptoms,
        first_aid=first_aid,
        children=normalize_text(data.get("children") or ""),
        pets=normalize_text(data.get("pets") or ""),
        severity=grade_severity(symptoms, toxin, quote),
        quote=quote,
        page=page,
    )


# --------------------------------------------------------------- заливка книги


async def save_toxicity(db, source_id: str, fact: "ToxicityFact", verified: bool) -> int:
    """Пишет одну запись об опасности. Повтор той же цитаты не ложится дважды."""
    from sqlalchemy import text as sql_text

    result = await db.execute(sql_text("""
        INSERT INTO houseplant_toxicity
            (taxon_latin, taxon_ru, toxin, parts, symptoms, first_aid,
             children, pets, severity, quote, source_id, page, latin_verified)
        VALUES (:latin, :ru, :toxin, :parts, :symptoms, :first_aid,
                :children, :pets, :severity, :quote, :source, :page, :verified)
        ON CONFLICT DO NOTHING
    """), {
        "latin": fact.taxon_latin, "ru": fact.taxon_ru, "toxin": fact.toxin,
        "parts": fact.parts, "symptoms": fact.symptoms, "first_aid": fact.first_aid,
        "children": fact.children, "pets": fact.pets, "severity": fact.severity,
        "quote": fact.quote, "source": source_id, "page": fact.page,
        "verified": verified,
    })
    return result.rowcount or 0


async def ingest_toxicity_book(source_id: str, book: str, *, limit: int = 0,
                               start_at: int = 0, apply: bool = True,
                               on_piece=None) -> dict:
    """Разбирает пособие по ядовитым комнатным целиком.

    Возобновляемая: ``start_at`` пропускает уже разобранные статьи, а
    ``on_piece`` сообщает о каждой — этим активность бьёт heartbeat.
    """
    from app.database import async_session
    from app.services.houseplant_care import resolve_latin
    from app.services.houseplant_ingest import open_book, page_text, save_source

    doc = open_book(book)
    # Пробному разбору вся книга не нужна: распознавать две сотни страниц ради
    # трёх статей значит ждать полчаса впустую. Берём начало с запасом.
    last_page = doc.page_count if not limit else min(doc.page_count, 12 + limit * 10)
    pages = []
    for number in range(1, last_page + 1):
        pages.append(await page_text(doc, number))
        if on_piece and number % 10 == 0:
            await on_piece({"stage": "чтение", "done": number, "total": last_page})

    articles = find_articles(pages)
    if limit:
        articles = articles[: start_at + limit]

    if apply:
        await save_source(source_id, book)

    found = saved = 0
    for index, (latin, russian, article, page) in enumerate(articles, 1):
        if index <= start_at:
            continue
        try:
            fact = await extract_toxicity(article, page=page)
        except Exception as error:
            if on_piece:
                await on_piece({"stage": "разбор", "done": index, "total": len(articles),
                                "error": str(error)[:200]})
            continue
        if fact is None:
            continue

        # Латынь пособия сверяем тем же судьёй, что и в слое ухода: иначе
        # опасность приклеится к растению с похожим именем.
        # Имя ищем тремя путями, по убыванию надёжности: латынь из заголовка,
        # если распознаватель её не испортил; латынь, восстановленная моделью;
        # и, наконец, поиск по русскому имени в iNaturalist — оно в заголовке
        # уцелело, даже когда латынь превратилась в «Адауе атепсапа».
        name = latin or fact.taxon_latin
        accepted, verified = await resolve_latin(name) if name else ("", False)
        if not verified:
            by_russian = await latin_by_russian_name(fact.taxon_ru or russian)
            if by_russian:
                accepted, verified = await resolve_latin(by_russian)
        fact.taxon_latin = accepted or name
        if not fact.taxon_ru:
            fact.taxon_ru = russian
        found += 1

        if apply:
            async with async_session() as db:
                saved += await save_toxicity(db, source_id, fact, verified)
                await db.commit()
        if on_piece:
            await on_piece({"stage": "разбор", "done": index, "total": len(articles),
                            "plant": fact.taxon_latin, "severity": fact.severity,
                            "found": found, "saved": saved})

    return {"source": source_id, "book": book, "articles": len(articles),
            "found": found, "saved": saved, "applied": apply}


async def latin_by_russian_name(name_ru: str) -> str:
    """Находит латинское имя по русскому названию через iNaturalist.

    Нужна потому, что распознаватель переводит латынь заголовка в кириллицу:
    «Агава американская — Адауе атепсапа». Русское имя при этом остаётся целым,
    а iNaturalist знает русские названия и отвечает принятой латынью — тем же
    способом мы уже чиним латынь в гербарии.
    """
    import httpx

    from app.services.inaturalist import INAT_BASE, _HEADERS

    query = (name_ru or "").strip()
    if len(query) < 4:
        return ""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{INAT_BASE}/taxa",
                params={"q": query, "is_active": "true", "per_page": 5, "locale": "ru"},
                headers=_HEADERS,
            )
            results = (response.json() or {}).get("results") or []
    except Exception:
        return ""

    wanted = query.lower()
    for row in results:
        # Берём только настоящее совпадение имени, а не первое, что похоже:
        # «агава американская» должна дать Agave americana, а не любую агаву.
        names = {(row.get("preferred_common_name") or "").lower(),
                 (row.get("english_common_name") or "").lower()}
        if wanted in names and row.get("rank") in ("species", "genus"):
            return (row.get("name") or "").strip()
    return ""
