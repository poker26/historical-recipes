"""Рассказ о растении из книги: родина, история, название, биология, поверья.

Слой ухода (``houseplant_care``) отвечает на вопрос «как держать». Карточке
этого мало: человек сфотографировал растение и хочет узнать, что это за
растение, откуда оно и чем примечательно. Такие сведения разбросаны по
научно-популярным книгам («Жизнь растений», Верзилин, Головкин, Боун), и
этот модуль вынимает их постранично.

Правило то же, что в уходе: факт без дословной цитаты со страницы не
записывается. Цитата сверяется с текстом страницы с поправкой на переносы:
распознанная страница хранит «филоденд-рон», а модель цитирует
«филодендрон», и без поправки терялся каждый пятый верный факт (проба
19.10.2026, «Жизнь растений», т. 6, с. 481).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from app.database import async_session
from app.services.houseplant_care import (
    HOUSEPLANT_BOOKS,
    normalize_latin,
    normalize_text,
    resolve_latin,
    verify_latin,
)
from app.services.houseplant_ingest import book_pieces, count_pieces, open_book, save_source
from app.services.llm import chat_completion_json

# Виды фактов. Ключ хранится в базе, значение — заголовок раздела карточки.
STORY_KINDS: dict[str, str] = {
    "homeland": "Родина и жизнь в природе",
    "history": "Как попало в дома",
    "name": "Откуда название",
    "biology": "Как растёт и цветёт",
    "uses": "Как используют",
    "lore": "Поверья и легенды",
    "danger": "Чем опасно",
    "lookalikes": "С чем путают",
}

_STORY_PROMPT = """Ты читаешь страницу научно-популярной книги о растениях. Нам нужны
сведения, которые интересно узнать человеку, сфотографировавшему это растение
у себя дома или на прогулке. Уход (полив, свет, пересадку, подкормку) НЕ бери,
он собран отдельно.

Верни СТРОГО JSON:
{"plants": [
  {"taxon": {"latin": "...", "ru": "...", "rank": "genus|species"},
   "facts": [
     {"kind": "<homeland|history|name|biology|uses|lore|danger|lookalikes>",
      "fact": "<одно-два предложения по-русски: сама мысль простыми словами>",
      "quote": "<дословный кусок текста страницы, на котором держится мысль, на языке книги>"}
   ]}
]}

Что значат виды:
- homeland: где растёт в природе, в каких условиях, какого размера там бывает;
- history: кто, когда и откуда привёз, как растение вошло в моду;
- name: откуда латинское или народное имя, что оно значит;
- biology: как цветёт, кто опыляет, как размножается в природе, необычные
  особенности строения или поведения, долголетие, рекорды;
- uses: как растение используют люди (еда, лекарство, волокно, ритуалы);
- lore: легенды, поверья, символика;
- danger: ядовитость, раздражение кожи, опасность для детей и животных;
- lookalikes: с каким ДРУГИМ растением его путают или сравнивают и как
  отличить. Книга должна назвать это другое растение. Приметы, по которым
  узнают сам вид, без сравнения с другим растением — это biology.

Правила:
1. Бери только то, что сказано про конкретный род или вид. Общие рассуждения
   про семейство отнеси к роду, только если книга прямо называет этот род.
2. Цитата берётся из текста БЕЗ изменений, слово в слово. Нет цитаты — нет факта.
3. Ничего не добавляй от себя: ни дат, ни стран, ни чисел, которых нет в тексте.
4. Сухое описание внешности (листья перистые, цветки мелкие) не нужно. Нужно
   то, что удивит или объяснит: зачем, почему, откуда.
5. Растение называй так, как его называет книга. Русского имени достаточно.
   Латынь распознавание часто портит: если её можно узнать, восстанови
   написание; если нельзя, оставь "latin" пустым, но факты всё равно верни.
6. Подписи к рисункам и таблицам пропускай.
7. Если книга на другом языке, "fact" всё равно пиши по-русски, а "quote" —
   на языке книги.
8. Если на странице правда нет ничего из перечисленного, верни {"plants": []}.

Текст:
---
{article}
---"""

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "ё": "е"})


def canon(raw: str) -> str:
    """Текст для сверки цитаты: без переносов, без регистра, с одинаковыми кавычками."""
    s = normalize_text(raw).lower().translate(_QUOTES)
    s = re.sub(r"(?<=\w)[-¬]\s*(?=\w)", "", s)      # «филоденд- рон», «Шри-Ланка» — одинаково с обеих сторон
    return re.sub(r"\s+", " ", s).strip()


def story_quote_grounded(quote: str, source_text: str) -> bool:
    """Есть ли цитата на странице, с точностью до переносов и кавычек."""
    q = canon(quote)
    return len(q) >= 20 and q in canon(source_text)


@dataclass
class StoryFact:
    kind: str
    fact: str
    quote: str
    page: int | None


async def extract_story(article: str, page: int | None = None) -> list[dict]:
    """Факты одной страницы: ``[{"taxon": {...}, "facts": [StoryFact]}]``.

    Всё, что не подтвердилось цитатой с этой же страницы, отброшено.
    """
    source_text = normalize_text(article)
    data = await chat_completion_json(
        [{"role": "user", "content": _STORY_PROMPT.replace("{article}", article)}],
        task="plant_extraction",
        temperature=0.1,
    )
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for block in data.get("plants") or []:
        if not isinstance(block, dict):
            continue
        facts: list[StoryFact] = []
        seen: set[str] = set()
        for item in block.get("facts") or []:
            if not isinstance(item, dict):
                continue
            kind = (item.get("kind") or "").strip()
            fact = (item.get("fact") or "").strip()
            quote = (item.get("quote") or "").strip()
            if kind not in STORY_KINDS or not fact or len(fact) > 600:
                continue
            if not story_quote_grounded(quote, source_text) or canon(quote) in seen:
                continue
            seen.add(canon(quote))
            facts.append(StoryFact(kind=kind, fact=fact, quote=quote, page=page))
        if not facts:
            continue
        taxon = dict(block.get("taxon") or {})
        latin = verify_latin(taxon.get("latin") or "", taxon.get("ru") or "", source_text)
        taxon["latin"] = normalize_latin(latin) if latin else ""
        taxon["ru"] = (taxon.get("ru") or "").strip()
        if not taxon["latin"] and not taxon["ru"]:
            continue
        out.append({"taxon": taxon, "facts": facts})
    return out


async def save_story(source_id: str, plant: dict) -> int:
    """Пишет факты одного растения. Возвращает, сколько строк легло."""
    taxon = plant["taxon"]
    latin, verified = (await resolve_latin(taxon["latin"])) if taxon["latin"] else ("", False)
    rows = 0
    async with async_session() as db:
        for f in plant["facts"]:
            result = await db.execute(text("""
                INSERT INTO houseplant_story
                    (source_id, page, taxon_latin, taxon_rank, taxon_ru, latin_verified, kind, fact, quote)
                VALUES (:source, :page, NULLIF(:latin, ''), :rank, NULLIF(:ru, ''), :verified,
                        :kind, :fact, :quote)
                ON CONFLICT DO NOTHING
            """), {
                "source": source_id, "page": f.page, "latin": latin,
                "rank": (taxon.get("rank") or "species").strip(), "ru": taxon["ru"],
                "verified": verified, "kind": f.kind, "fact": f.fact, "quote": f.quote,
            })
            rows += result.rowcount or 0
        await db.commit()
    return rows


async def ingest_story_book(
    source_id: str,
    *,
    pages: list[int] | None = None,
    limit: int = 0,
    start_at: int = 0,
    apply: bool = True,
    on_piece: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Разбирает книгу постранично и пишет факты. Возобновляется с ``start_at``."""
    meta = HOUSEPLANT_BOOKS.get(source_id) or {}
    book = meta.get("object")
    if not book:
        raise ValueError(f"не знаю, какой файл разбирать для источника {source_id}")
    doc = open_book(book)
    numbers = pages or list(range(1, doc.page_count + 1))
    total = await count_pieces(doc, False, numbers)
    if apply:
        await save_source(source_id, book)

    done = plants = facts = saved = 0
    async for body, page, _genus in book_pieces(doc, False, numbers):
        done += 1
        if done <= start_at:
            continue
        if limit and done > start_at + limit:
            break
        try:
            found = await extract_story(body, page=page)
        except Exception as error:          # одна страница не должна валить прогон
            if on_piece:
                await on_piece({"done": done, "total": total, "page": page, "error": str(error)[:200]})
            continue
        for plant in found:
            plants += 1
            facts += len(plant["facts"])
            if apply:
                saved += await save_story(source_id, plant)
        if on_piece:
            await on_piece({"done": done, "total": total, "page": page,
                            "plants": plants, "facts": facts, "saved": saved})
    return {"source": source_id, "book": book, "pieces": done, "total": total,
            "plants": plants, "facts": facts, "saved": saved}
