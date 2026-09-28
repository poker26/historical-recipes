"""Названия рецептов по составу вместо безликих «Сбор № 1».

Зачем. В книгах рецепты часто пронумерованы внутри раздела: «Сбор № 1», «Сбор № 2»,
«Рецепт 5». На сайте у каждого рецепта своя страница, и таких заголовков в индексе
десятки одинаковых (замер 28.09: 325 рецептов с безликим названием среди 26 924
индексируемых, «сбор № 1» у 55 рецептов). Поисковику и читателю название должно
говорить, что это за средство: «Сбор из ромашки, мяты и зверобоя».

Как. Модель читает текст рецепта и список ингредиентов и предлагает название: форма
средства из текста, главные растения, назначение, только если оно прямо названо.
Предложение проходит проверку: каждое значимое слово названия должно найтись в тексте
рецепта или в названиях ингредиентов (по основе слова, без окончания). Не прошло,
название остаётся прежним, а случай попадает в сводку. Каждое переименование пишется
в processing_log (шаг ``recipe_rename``) со старым названием, откат по журналу.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Callable

from sqlalchemy import text

from app.database import async_session
from app.services import llm as llm_svc

logger = logging.getLogger(__name__)

LOG_STEP = "recipe_rename"
Progress = Callable[[dict], None]

GENERIC = re.compile(
    r"^\s*(№\s*\d+\.?\s*)?(сбор|чай|настой|настойка|отвар|мазь|смесь|порошок|рецепт|средство|"
    r"микстура|сироп|бальзам|припарка|компресс|ванна|полоскание|примочка|эликсир|капли|"
    r"напиток|питьё|питье|лекарство|пилюли|состав)\b"
    r"(\s*(№|n|no)?\s*\d+[а-я]?\.?)?"
    r"(\s+(из|от|при|для|на)\s+(корня|корней|листьев|листа|травы|трав|цветов|цветков|коры|семян|ягод|"
    r"плодов|почек|корневищ|корневища|верхушек|шишек|смеси))?"
    r"\s*[.,;:]?\s*$", re.I)

# Слова, которые в названии могут стоять без опоры в тексте: форма средства, связки.
_FREE = {
    "сбор", "сбора", "чай", "чая", "настой", "настоя", "настойка", "настойки", "отвар", "отвара", "мазь",
    "мази", "смесь", "смеси", "порошок", "сироп", "бальзам", "припарка", "компресс", "ванна", "полоскание",
    "примочка", "эликсир", "капли", "напиток", "питьё", "питье", "средство", "масло", "вино", "водка",
    "из", "от", "при", "для", "на", "с", "со", "и", "в", "во", "по", "к", "а", "или", "без",
    "лекарственный", "лекарственная", "лекарственное", "лечебный", "лечебная", "лечебное", "домашний",
    "травяной", "травяная", "трав", "травы", "травой", "корня", "корней", "листьев", "цветков", "цветов",
    "коры", "семян", "ягод", "плодов", "почек", "корневищ", "корневища",
}

_PROMPT = """Ты редактор справочника старинных рецептов. У рецепта безликое название вроде «Сбор № 1»:
по нему не понять, что это. Дай рецепту короткое понятное название.

Правила:
1. Форма средства (сбор, чай, настой, отвар, настойка, мазь, сироп и т. п.) та, что в тексте.
2. Назови главные растения или вещества из рецепта, не больше трёх, в родительном падеже
   после «из»: «Сбор из ромашки, мяты и зверобоя». Только то, что есть в тексте или в
   списке ингредиентов. Ничего не добавляй от себя.
3. Назначение («при кашле», «успокоительный») добавляй, только если оно прямо сказано в
   тексте рецепта.
4. Не длиннее 70 знаков, без номера, в современной орфографии, с заглавной буквы.
5. Если по тексту нельзя понять состав, верни пустую строку.

Верни JSON: {"name": "..."}"""


def _words(s: str) -> list[str]:
    return re.findall(r"[а-яa-z]+", (s or "").lower().replace("ё", "е"))


def _stem_len(w: str) -> int:
    """Длина основы для сравнения: слово без двух последних букв (окончание), от 3 до 6
    букв. «мяты» и «мята» сходятся на «мят», «зверобоя» и «зверобой» на «зверов»."""
    return max(3, min(6, len(w) - 2))


def grounded(name: str, source: str) -> tuple[bool, list[str]]:
    """Каждое значимое слово названия есть в тексте рецепта: его основа совпадает с
    началом какого-нибудь слова текста."""
    have: set[str] = set()
    for w in _words(source):
        for k in range(3, min(6, len(w)) + 1):
            have.add(w[:k])
    missing = [w for w in _words(name)
               if len(w) >= 3 and w not in _FREE and w[:_stem_len(w)] not in have]
    return (not missing), missing


def clean_name(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip().strip("«»\"'").rstrip(".")
    s = re.sub(r"^(№\s*)?\d+[.)]?\s*", "", s)
    return s[:1].upper() + s[1:] if s else s


async def candidates(limit: int = 0, force: bool = False) -> list[dict]:
    """Индексируемые рецепты (как в карте сайта) с безликим названием, ещё не переименованные."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT r.id, r.book_id, r.name, r.original_text, r.normalized_text, r.category,
                   coalesce((SELECT json_agg(coalesce(nullif(ri.name, ''), ri.original_name))
                             FROM recipe_ingredients ri WHERE ri.recipe_id = r.id), '[]') AS ingredients
            FROM recipes r
            WHERE r.home_doable AND coalesce(r.procedure_score, 0) >= 2 AND r.name IS NOT NULL
              AND length(coalesce(r.original_text, '')) >= 200
              AND (CAST(:force AS boolean) OR NOT EXISTS (
                    SELECT 1 FROM processing_log l WHERE l.step = :s AND l.details->>'recipe_id' = r.id::text))
            ORDER BY r.created_at, r.id"""), {"s": LOG_STEP, "force": force})).all()
    out = []
    for r in rows:
        if not GENERIC.match(r.name or ""):
            continue
        ing = r.ingredients if isinstance(r.ingredients, list) else json.loads(r.ingredients or "[]")
        out.append({"id": str(r.id), "book_id": str(r.book_id), "name": r.name, "category": r.category,
                    "text": (r.normalized_text or r.original_text or "")[:2500],
                    "original": (r.original_text or "")[:2500],
                    "ingredients": [i for i in ing if i]})
        if limit and len(out) >= limit:
            break
    return out


async def propose(c: dict) -> dict:
    user = (f"Нынешнее название: {c['name']}\n"
            f"Раздел: {c['category'] or 'не указан'}\n"
            f"Ингредиенты: {', '.join(c['ingredients'][:20]) or 'не выделены'}\n\n"
            f"Текст рецепта:\n{c['text']}")
    res = await llm_svc.chat_completion_json(
        [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user}],
        task="recipe_extraction", temperature=0.1, max_tokens=300)
    name = clean_name(res.get("name", "") if isinstance(res, dict) else "")
    if not name or len(name) > 80 or GENERIC.match(name):
        return {"ok": False, "why": "пусто или снова безлико", "name": name}
    source = " ".join([c["text"], c["original"], " ".join(c["ingredients"])])
    ok, missing = grounded(name, source)
    return {"ok": ok, "name": name, "why": None if ok else f"нет в тексте: {', '.join(missing)}"}


async def apply_name(c: dict, new: str, by: str) -> bool:
    async with async_session() as db:
        res = await db.execute(text("UPDATE recipes SET name = :n WHERE id = CAST(:i AS uuid) AND name = :o"),
                               {"n": new, "i": c["id"], "o": c["name"]})
        if not res.rowcount:
            await db.rollback()
            return False
        details = {"recipe_id": c["id"], "before": c["name"], "after": new, "by": by}
        await db.execute(text(
            "INSERT INTO processing_log (id, book_id, step, status, details, created_at) "
            "VALUES (CAST(:i AS uuid), CAST(:b AS uuid), :s, 'completed', CAST(:d AS jsonb), now())"),
            {"i": str(uuid.uuid4()), "b": c["book_id"], "s": LOG_STEP, "d": json.dumps(details, ensure_ascii=False)})
        await db.commit()
    return True


async def run_recipe_rename(apply: bool = False, limit: int = 0, force: bool = False,
                            progress: Progress | None = None) -> dict:
    """Сухой прогон (apply=False) только предлагает и проверяет названия."""
    items = await candidates(limit=limit, force=force)
    by = "recipe-rename" + ("" if apply else "-dry")
    stats = {"apply": apply, "candidates": len(items), "renamed": 0, "proposed": 0, "rejected": 0,
             "failed": 0, "sample": [], "rejected_sample": []}
    for n, c in enumerate(items, 1):
        try:
            p = await propose(c)
        except Exception as e:  # noqa: BLE001 — сбой модели на одном рецепте не валит прогон
            logger.warning("recipe rename %s: %s", c["id"], e)
            stats["failed"] += 1
            continue
        if not p["ok"]:
            stats["rejected"] += 1
            if len(stats["rejected_sample"]) < 30:
                stats["rejected_sample"].append({"id": c["id"], "old": c["name"], "new": p["name"], "why": p["why"]})
            continue
        stats["proposed"] += 1
        if len(stats["sample"]) < 60:
            stats["sample"].append({"id": c["id"], "old": c["name"], "new": p["name"]})
        if apply and await apply_name(c, p["name"], by):
            stats["renamed"] += 1
        if progress:
            progress({k: v for k, v in stats.items() if not isinstance(v, list)} | {"done": n})
    return stats


async def revert_recipe_rename(by_prefix: str = "recipe-rename") -> dict:
    """Вернуть прежние названия, если с тех пор их никто не менял."""
    reverted = skipped = 0
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT id, details FROM processing_log WHERE step = :s AND status = 'completed' "
            "AND details->>'by' = :p ORDER BY created_at DESC"), {"s": LOG_STEP, "p": by_prefix})).all()
        for r in rows:
            d = r.details or {}
            res = await db.execute(text(
                "UPDATE recipes SET name = :b WHERE id = CAST(:i AS uuid) AND name = :a"),
                {"b": d.get("before"), "a": d.get("after"), "i": d.get("recipe_id")})
            if res.rowcount:
                await db.execute(text("UPDATE processing_log SET status = 'reverted' WHERE id = :i"), {"i": r.id})
                reverted += 1
            else:
                skipped += 1
        await db.commit()
    return {"reverted": reverted, "skipped": skipped}
