"""LLM-based recipe extraction from identified recipe sections.

Takes recipe block text and extracts structured recipes with ingredients.
"""

from dataclasses import dataclass, field

from app.services.llm import chat_completion_json


MAX_CHARS_PER_CALL = 100_000  # ~50K tokens, safe for extraction with response


@dataclass
class ExtractedIngredient:
    name: str  # normalized nominative case
    amount: str = ""
    unit: str = ""
    original: str = ""  # as written in the source text


@dataclass
class ExtractedRecipe:
    name: str
    category: str = "другое"
    original_text: str = ""
    ingredients: list[ExtractedIngredient] = field(default_factory=list)


SYSTEM_PROMPT = """You are extracting individual recipes from a section of a historical Russian recipe book \
about herbal tinctures, distillates, and medicinal preparations.

Be PRECISE — extract ONLY actual recipes that contain ingredients and/or preparation instructions. \
Do NOT extract:
- Chapter headers or section dividers
- Bibliography or reference entries
- Table of contents entries
- General descriptions or introductory text
- Footnotes or editorial comments

For each recipe, extract:
- name: the recipe name/title
- category: one of водка, ликёр, настойка, бальзам, масло, вода, эссенция, эликсир, тинктура, ратафия, розолия, другое
- original_text: the COMPLETE original recipe text (including name, ingredients, and instructions)
- ingredients: array of extracted ingredients, each with:
  - name: ingredient name in nominative case (именительный падеж), e.g. "корица" not "корицы"
  - amount: numeric amount as string, e.g. "2", "0.5"
  - unit: measurement unit, e.g. "золотник", "фунт", "ведро", "штоф", "бутылка", "стакан"
  - original: the ingredient phrase as written in the original text

Return a JSON array of recipe objects.
Important: preserve the EXACT original text of each recipe. Do not modify, correct, or modernize it."""


async def extract_recipes_from_section(
    section_text: str,
    book_title: str = "",
    recipe_pattern: str | None = None,
) -> list[ExtractedRecipe]:
    """Extract structured recipes from a recipe section using LLM.

    If the section is too large for a single call, splits it and processes in parts.
    """
    if len(section_text) <= MAX_CHARS_PER_CALL:
        return await _extract_single(section_text, book_title, recipe_pattern)

    # Split large sections
    all_recipes = []
    lines = section_text.split("\n")
    chunk_lines = []
    chunk_len = 0

    for line in lines:
        chunk_lines.append(line)
        chunk_len += len(line) + 1
        if chunk_len >= MAX_CHARS_PER_CALL * 0.8:
            chunk_text = "\n".join(chunk_lines)
            recipes = await _extract_single(chunk_text, book_title, recipe_pattern)
            all_recipes.extend(recipes)
            chunk_lines = []
            chunk_len = 0

    if chunk_lines:
        chunk_text = "\n".join(chunk_lines)
        recipes = await _extract_single(chunk_text, book_title, recipe_pattern)
        all_recipes.extend(recipes)

    return all_recipes


async def _extract_single(
    text: str,
    book_title: str,
    recipe_pattern: str | None,
) -> list[ExtractedRecipe]:
    """Extract recipes from a single text chunk."""
    pattern_hint = ""
    if recipe_pattern:
        pattern_hint = f"\nThe recipes in this section use the formatting pattern: {recipe_pattern}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f'Here is a recipe section from "{book_title}".{pattern_hint}\n\n'
            f"Extract all individual recipes:\n\n{text}"
        )},
    ]

    result = await chat_completion_json(
        messages,
        task="recipe_extraction",
        temperature=0.1,
        max_tokens=32768,
    )

    recipes = result if isinstance(result, list) else result.get("recipes", [])
    return [_parse_recipe(r) for r in recipes if _is_valid_recipe(r)]


def _is_valid_recipe(data: dict) -> bool:
    """Check if extracted data looks like a real recipe."""
    name = data.get("name", "").strip()
    if not name or len(name) < 3:
        return False
    text = data.get("original_text", "")
    if len(text) < 30:
        return False
    return True


def _parse_recipe(data: dict) -> ExtractedRecipe:
    """Parse a recipe dict from LLM response."""
    ingredients = []
    for ing_data in data.get("ingredients", []):
        ingredients.append(ExtractedIngredient(
            name=ing_data.get("name", ""),
            amount=str(ing_data.get("amount", "")),
            unit=ing_data.get("unit", ""),
            original=ing_data.get("original", ""),
        ))

    return ExtractedRecipe(
        name=data.get("name", "").strip(),
        category=data.get("category", "другое"),
        original_text=data.get("original_text", ""),
        ingredients=ingredients,
    )
