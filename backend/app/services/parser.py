"""Recipe parser: extract structured recipes from text chunks.

Consolidates parsing logic from multiple n8n Code nodes into a single
module. Supports multiple source formats (**, §, numbered, plain).

Categories: водка, ликёр, настойка, бальзам, масло, вода, эссенция, эликсир,
            тинктура, ратафия, розолия
"""

import re
from dataclasses import dataclass, field


@dataclass
class ParsedIngredient:
    name: str
    original_name: str = ""
    amount: str = ""
    unit: str = ""


@dataclass
class ParsedRecipe:
    name: str
    category: str = ""
    text: str = ""
    ingredients: list[ParsedIngredient] = field(default_factory=list)
    number: int = 0
    source_pattern: str = ""


# === Category detection ===

CATEGORY_KEYWORDS = {
    "водка": ["водка", "водку", "водки"],
    "ликёр": ["ликер", "ликёр", "ликеру", "ликёру"],
    "настойка": ["настойка", "настойку", "настойки", "наливка", "наливку"],
    "бальзам": ["бальзам", "бальзаму"],
    "масло": ["масло", "масла", "маслу"],
    "вода": ["вода", "воду", "воды", "благовонн"],
    "эссенция": ["эссенция", "эссенцию", "эссенции"],
    "эликсир": ["эликсир", "эликсиру"],
    "тинктура": ["тинктура", "тинктуру"],
    "ратафия": ["ратафия", "ратафию", "ратафии"],
    "розолия": ["розолия", "розолию", "розолии"],
}


def detect_category(text: str) -> str:
    """Detect recipe category from text content."""
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category
    return "другое"


# === Ingredient extraction ===

# Pattern: "number unit_name ingredient_name"
# Example: "2 золотника корицы", "1 фунт сахару", "3 штофа воды"
INGREDIENT_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s+"
    r"(золотник[аоев]*|фунт[аоев]*|лот[аоев]*|пуд[аоев]*|"
    r"ведр[аоев]*|штоф[аоев]*|бутыл[а-я]*|чар[а-я]*|"
    r"горст[а-я]*|щепот[а-я]*|капел[а-я]*|стакан[а-я]*|"
    r"ложк[а-я]*|гарн[а-я]*|четверт[а-я]*)\s+"
    r"([а-яА-ЯёЁ][а-яА-ЯёЁ\s]{2,40}?)(?=[.,;:\n]|$)",
    re.IGNORECASE,
)


def extract_ingredients(text: str) -> list[ParsedIngredient]:
    """Extract ingredients with quantities from recipe text."""
    ingredients = []
    for m in INGREDIENT_PATTERN.finditer(text):
        ingredients.append(ParsedIngredient(
            name=m.group(3).strip(),
            original_name=m.group(3).strip(),
            amount=m.group(1),
            unit=m.group(2),
        ))
    return ingredients


# === Main parsing ===

def parse_recipes(text: str) -> list[ParsedRecipe]:
    """Parse recipes from a text block.

    Detects format automatically and extracts structured recipes.
    """
    from app.services.postprocessor import detect_chunk_boundaries, split_into_chunks

    # Detect and split by boundaries
    boundaries = detect_chunk_boundaries(text)
    chunks = split_into_chunks(text, boundaries)

    recipes = []
    for chunk in chunks:
        name = chunk.get("title", "").strip()
        if not name:
            # Only try to extract name if this chunk came from a detected boundary
            if chunk.get("pattern") in (None, "", "none"):
                continue
            # Try to extract name from first line
            first_line = chunk["text"].split("\n")[0].strip()
            # Remove numbering
            name = re.sub(r"^[\d§№.*]+[.)]\s*", "", first_line)
            name = name.strip("* .")

        if not name or len(name) < 3:
            continue

        recipe = ParsedRecipe(
            name=name,
            text=chunk["text"],
            category=detect_category(chunk["text"]),
            number=chunk.get("number", 0),
            source_pattern=chunk.get("pattern", ""),
            ingredients=extract_ingredients(chunk["text"]),
        )
        recipes.append(recipe)

    return recipes
