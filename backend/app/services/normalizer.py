"""Text normalization: pre-reform orthography, old plant names, measures.

Handles the conversion of pre-1918 Russian orthography to modern,
old plant names to modern equivalents, and historical measurement units.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dictionary import DictionaryTerm


# === Orthographic rules (hardcoded, no dictionary needed) ===

# Pre-reform letter replacements
ORTHO_REPLACEMENTS = {
    "ѣ": "е",
    "Ѣ": "Е",
    "ѳ": "ф",
    "Ѳ": "Ф",
    "і": "и",  # Cyrillic і (U+0456)
    "І": "И",  # Cyrillic І (U+0406)
    "i": "и",  # Latin i used as і in some OCR outputs
}


def normalize_orthography(text: str) -> str:
    """Convert pre-reform Russian orthography to modern.

    Applies fixed rules (ѣ→е, і→и, ѳ→ф) and removes trailing ъ.
    """
    result = text

    # Apply letter replacements
    for old, new in ORTHO_REPLACEMENTS.items():
        result = result.replace(old, new)

    # Remove trailing ъ (hard sign at end of words)
    # But keep ъ when followed by a vowel (e.g., "объём", "въездъ")
    result = re.sub(r"ъ(?=[^а-яА-ЯёЁ]|$)", "", result)

    return result


async def normalize_with_dictionary(
    text: str,
    db: AsyncSession,
    categories: list[str] | None = None,
) -> str:
    """Normalize text using dictionary terms from database.

    Loads terms from specified categories and applies replacements.
    """
    query = select(DictionaryTerm)
    if categories:
        query = query.where(DictionaryTerm.category.in_(categories))
    # Order by term length descending to replace longer terms first
    query = query.order_by(DictionaryTerm.term_old.desc())

    result = await db.execute(query)
    terms = result.scalars().all()

    normalized = text
    for term in terms:
        # Case-insensitive replacement preserving surrounding text
        pattern = re.compile(re.escape(term.term_old), re.IGNORECASE)
        normalized = pattern.sub(term.term_modern, normalized)

    return normalized


async def full_normalize(text: str, db: AsyncSession) -> str:
    """Full normalization pipeline.

    1. Orthographic normalization (fixed rules)
    2. Dictionary-based normalization (plants, techniques)
    3. Measurement conversion (inline)
    """
    # Step 1: Orthography
    result = normalize_orthography(text)

    # Step 2: Dictionary-based (plants and techniques)
    result = await normalize_with_dictionary(result, db, categories=["plants", "techniques"])

    # Step 3: Inline measurement conversion
    result = _normalize_measures_inline(result)

    return result


def _normalize_measures_inline(text: str) -> str:
    """Convert historical measures inline, keeping original in parentheses.

    "2 золотника" -> "2 золотника (8.54 г)"
    """
    from app.utils.measures import MEASURES

    for unit_name, conversion in MEASURES.items():
        # Match: number + unit name (with possible ъ endings, plural forms)
        pattern = re.compile(
            rf"(\d+(?:[.,]\d+)?)\s*({re.escape(unit_name)}[аоеуиыъь]*)",
            re.IGNORECASE,
        )
        def _replace(m, conv=conversion):
            value = float(m.group(1).replace(",", "."))
            if conv.get("type") == "multiplier":
                modern_value = value * conv["value"]
            else:
                modern_value = value * conv["value"]
            # Format nicely
            if modern_value >= 1:
                modern_str = f"{modern_value:.1f}"
            else:
                modern_str = f"{modern_value:.2f}"
            return f"{m.group(0)} ({modern_str} {conv['unit']})"

        text = pattern.sub(_replace, text)

    return text
