# -*- coding: utf-8 -*-
"""Phase A of recipe #2-structuring — decouple SHARED-PASSAGE recipes.

Encyclopedic / classical травники store the whole source PARAGRAPH as each extracted remedy's
`original_text`, so several distinct cards («Сушеная желчь коршуна для глаз», «Мозг коршуна с
пореем», «Зола перьев коршуна») all show the same multi-remedy block. This distils each card's
SPECIFIC remedy span from the shared passage into `recipes.normalized_text`.

STRICTLY EXTRACTIVE, NOT generative (the hallucination-guard distinction): the LLM only SELECTS
which existing sentences of the passage belong to a given remedy — it invents nothing. A
grounding gate then rejects any returned span that introduces words absent from the passage
(tolerating OCR/whitespace normalisation), so a fabricated or off-passage span is dropped and
the recipe simply keeps its `original_text` fallback.
"""
import re

from app.services.llm import chat_completion_json

_TOK = re.compile(r"[а-яёa-z0-9]{3,}", re.I)


def _tokens(s: str) -> list[str]:
    return _TOK.findall((s or "").lower())


def grounded(span: str, passage: str, max_novel: float = 0.15) -> bool:
    """A span is grounded iff it is a SUB-PART of the passage (not longer) whose words are
    almost all present in the passage — tolerates light OCR/whitespace fixes, rejects
    fabrication (an invented remedy injects many novel tokens)."""
    st = _tokens(span)
    if not st or len(span) > len(passage) * 1.1:
        return False
    pset = set(_tokens(passage))
    novel = sum(1 for t in st if t not in pset)
    return novel / len(st) <= max_novel


_SYS = (
    "Ты — точный экстрактор текста. Тебе дают ИСХОДНЫЙ ОТРЫВОК из старинной книги и "
    "пронумерованный список РЕЦЕПТОВ — каждый называет ОДНО конкретное средство, описанное "
    "ВНУТРИ этого отрывка. Для каждого рецепта верни ДОСЛОВНО те предложения из отрывка, что "
    "относятся ИМЕННО к этому средству (его приготовление и применение). Копируй текст из "
    "отрывка без изменений. НИЧЕГО не придумывай, не обобщай, не переписывай своими словами, "
    "не добавляй. Если средство рецепта в отрывке не описано — верни для него пустую строку. "
    'Ответ строго JSON: {"spans":[{"i":<номер_рецепта>,"text":"<дословный фрагмент отрывка>"}]}'
)


async def destructure_passage(passage: str, items: list[tuple[str, str]]) -> dict[str, str | None]:
    """`items` = [(recipe_id, name), …] all sharing `passage`. Returns {recipe_id: span or None}
    — None where the LLM found nothing or the span failed the grounding gate (→ caller keeps
    original_text)."""
    result: dict[str, str | None] = {rid: None for rid, _ in items}
    lines = "\n".join(f"{i}. {name}" for i, (_, name) in enumerate(items))
    user = f"ОТРЫВОК:\n{passage}\n\nРЕЦЕПТЫ:\n{lines}"
    out = await chat_completion_json(
        [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        task="recipe_extraction", temperature=0.1, max_tokens=min(8000, 1200 + len(passage)))
    spans = out.get("spans") if isinstance(out, dict) else (out if isinstance(out, list) else [])
    for s in spans or []:
        if not isinstance(s, dict):
            continue
        try:
            i = int(s.get("i"))
        except (TypeError, ValueError):
            continue
        span = (s.get("text") or "").strip()
        if 0 <= i < len(items) and span and grounded(span, passage):
            result[items[i][0]] = span
    return result
