"""LLM-based recipe extraction from identified recipe sections.

Takes recipe block text and extracts structured recipes with ingredients.
"""

import re
from dataclasses import dataclass, field

from app.services.llm import chat_completion_json


# Keep input small: extraction OUTPUT (full original_text duplicated + structured
# ingredients per recipe) runs ~3x the input, and Cyrillic tokenizes densely, so a
# big section overruns max_tokens and the JSON gets truncated mid-array. ~12K chars
# of input keeps the JSON response inside the 32K-token budget.
MAX_CHARS_PER_CALL = 12_000

# Whitelist of recipe categories. Anything the LLM returns outside this set
# (e.g. "дистиллят", "кальвадос") collapses to "другое" so the facet stays clean.
# Two groups: alcoholic/culinary preparations and medicinal preparations — the
# latter is needed because herbalism books contain genuine recipes (decoctions,
# teas, herbal collections) that are NOT водки/ликёры.
VALID_CATEGORIES = {
    # alcoholic preparations
    "водка", "ликёр", "настойка", "бальзам", "масло", "вода",
    "эссенция", "эликсир", "тинктура", "ратафия", "розолия",
    # medicinal preparations (herbalism)
    "отвар", "настой", "чай", "сбор", "мазь", "сироп",
    "порошок", "припарка", "капли", "примочка",
    # culinary dishes (food cookbooks / foraging books)
    "суп", "салат", "закуска", "горячее", "гарнир", "соус",
    "напиток", "варенье", "заготовка", "выпечка", "десерт",
    "каша", "приправа",
    "другое",
}

# A real recipe has at least this much body text. Below it we're almost always
# looking at a stray section header the LLM mistook for a recipe
# (e.g. "Оборудование", "Сырьё", "Пропорции").
MIN_RECIPE_TEXT_LEN = 150

# A real recipe describes a PREPARATION PROCESS — how to make the thing. Species
# descriptions in a field guide / determiner (a fungus or plant atlas) have only
# morphology + an edibility note and NO process. The catch: that edibility note is
# phrased with the SAME roots as cooking words, but in the INSTRUMENTAL case as
# adjectives — "употребляется варёным, солёным, маринованным" — whereas a real
# recipe uses those roots as VERBS (infinitive/imperative/past): "отварить,
# посолить, замариновать". So every pattern below is anchored on a verb ending
# (-ть / -л / -и imperative …) and must NOT match the -ным/-ым adjective forms.
# We keep allowing genuine zero-ingredient prose recipes (medicinal collections),
# but only if they carry such a verb; otherwise a zero-ingredient candidate is
# almost certainly a misclassified species description.
_VB = r"(?:ть|ться|л[аио]?|ли|ло|те|й|йте|ите|в|вши|)\b"  # verb endings (trailing ''=bare imperative); \b stops noun forms like "отвари-в-ания"
_PREP_VERB_RE = re.compile(
    r"насто(?:ять|ял|яв|й\b|и\b|йте)|настаива" + _VB + r"|"
    r"отвари" + _VB + r"|свари" + _VB + r"|\bвари" + _VB + r"|"
    r"кипяти" + _VB + r"|прокипяти" + _VB + r"|"
    r"смеша" + _VB + r"|переме[шс]а" + _VB + r"|размеша" + _VB + r"|"
    r"перегна" + _VB + r"|перегони\b|"
    r"процеди" + _VB + r"|процежива" + _VB + r"|сцеди" + _VB + r"|отцеди" + _VB + r"|"
    r"зали" + _VB + r"|залива" + _VB + r"|зале[йю]\b|"
    r"добави" + _VB + r"|добавля" + _VB + r"|"
    r"измельчи" + _VB + r"|истол(?:очь|ки\b|кут)|растол(?:очь|кут)|перетер(?:еть|ли)|разотр(?:и\b|ите)|"
    r"нареза" + _VB + r"|нареж" + _VB + r"|"
    r"высуши" + _VB + r"|насуши" + _VB + r"|подсуши" + _VB + r"|"
    r"посоли" + _VB + r"|засоли" + _VB + r"|присоли" + _VB + r"|"
    r"замаринова" + _VB + r"|промаринова" + _VB + r"|\bмаринова" + _VB + r"|"
    r"туши" + _VB + r"|потуши" + _VB + r"|"
    r"жари" + _VB + r"|пожари" + _VB + r"|обжари" + _VB + r"|зажари" + _VB + r"|"
    r"запе(?:чь|ка" + _VB + r")|испе(?:чь|ки\b)|выпе(?:чь|ка" + _VB + r")|"
    r"замочи" + _VB + r"|вымочи" + _VB + r"|намочи" + _VB + r"|"
    r"развести|развед[иё]" + r"|разбави" + _VB + r"|"
    r"остуди" + _VB + r"|охлади" + _VB + r"|"
    r"профильтрова" + _VB + r"|отфильтрова" + _VB + r"|"
    r"отжа" + _VB + r"|выжа" + _VB + r"|"
    r"выдержа" + _VB + r"|заква(?:сить|сил|шива" + _VB + r")|"
    r"нагре" + _VB + r"|подогре" + _VB + r"|посыпа" + _VB + r"|обваля" + _VB,
    re.IGNORECASE,
)


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


SYSTEM_PROMPT = """You are extracting individual recipes from a section of a Russian book. The book may be \
about herbal tinctures, distillates and medicinal preparations; OR a culinary / foraging cookbook (dishes \
prepared from wild or cultivated plants — soups, salads, drinks, preserves, baking); OR a historical \
(pre-1918) manual. Extract only what the section actually contains, in whatever wording and orthography \
it is written — do NOT assume the recipes are medicinal.

A "recipe" is any self-contained instruction for making a preparation. It can take EITHER form:
- STRUCTURED: a name followed by a list or table of ingredients with quantities, and/or numbered steps.
- PROSE: a narrative paragraph describing what to take and what to do \
(e.g. "Возьми фунтъ кардамона, настаивай на спирту три дня, потомъ перегони и процеди"), \
even without an itemised ingredient list.
Both forms are EQUALLY valid recipes. Some recipes emphasise INGREDIENTS (precise quantities matter), \
others emphasise the PROCESS (steps: смешать, настоять, перегнать, процедить, слить). Capture BOTH kinds — \
never skip a recipe just because it is written as prose or lacks a clean ingredient list, and never skip one \
just because it is mostly a table with little prose.

Be PRECISE about what is NOT a recipe. Do NOT extract:
- Chapter headers or section dividers
- Bibliography or reference entries
- Table of contents entries
- Purely general/introductory descriptions that give no way to make anything
- Footnotes or editorial comments
- A SPECIES DESCRIPTION from a field guide / determiner / atlas: morphology (cap, stem, flesh, \
colour, smell, size), habitat / where it grows, season, and an EDIBILITY note. An edibility statement \
or a "употребляется …" phrase (e.g. "съедобен", "ядовит", "употребляется варёным, солёным, маринованным") \
describes the ORGANISM itself, NOT a way to prepare a dish — it is NOT a recipe. Extract a recipe ONLY when \
the text gives an actual preparation METHOD: concrete steps (настоять, отварить, смешать, посолить, \
замариновать, перегнать, процедить …) and/or a real list of ingredients for making a specific dish or preparation.

CRITICAL — extract only what is literally present; never invent:
- Extract ONLY recipes that physically appear in the provided text. NEVER invent, complete, infer, or \
compose a recipe from your own knowledge of herbal medicine or distillation.
- If a passage merely DESCRIBES a plant or ingredient (its properties, history, habitat, indications) \
without giving a concrete preparation method, extract NOTHING from it.
- Reproduce original_text EXACTLY as written in the source — same wording AND same orthography. NEVER rewrite \
modern Russian into pre-1918 (archaic) spelling, and never modernize archaic text. If the source is in modern \
Russian, original_text MUST be in modern Russian.

For each recipe, extract:
- name: the recipe's title. If the source gives an explicit title, use it verbatim. \
If the recipe has NO title (common for medicinal herbal collections that are just a list of herbs \
with doses and a way to take them), SYNTHESIZE a short descriptive Russian name (3-7 words) from its \
PURPOSE/indication and/or its main ingredients — e.g. "Успокоительный сбор с валерианой", \
"Отвар при кашле с липой и малиной", "Желудочный чай". \
NEVER use the raw ingredient list (with grams) as the name — that is not a name.
- category: one of водка, ликёр, настойка, бальзам, масло, вода, эссенция, эликсир, тинктура, ратафия, розолия, \
отвар, настой, чай, сбор, мазь, сироп, порошок, припарка, капли, примочка, \
суп, салат, закуска, горячее, гарнир, соус, напиток, варенье, заготовка, выпечка, десерт, каша, приправа, другое. \
Choose by what the recipe actually IS: for herbal teas/decoctions/collections prefer отвар/настой/чай/сбор; \
for alcoholic preparations use водка/ликёр/настойка/…; for FOOD dishes use the culinary categories \
(суп/салат/закуска/горячее/гарнир/соус/напиток/варенье/заготовка/выпечка/десерт/каша/приправа). \
Use "другое" only when none fit.
- original_text: the COMPLETE recipe text copied VERBATIM from the source above (name + ingredients + the full \
preparation process). It must be a literal quote — every sentence must be findable in the provided text.
- ingredients: array of ingredients you can identify — parse them out of prose too when possible. \
If a recipe genuinely has no separable ingredients, return an empty array but STILL output the recipe. \
Each ingredient with:
  - name: ingredient name in nominative case (именительный падеж), e.g. "корица" not "корицы"
  - amount: numeric amount as string, e.g. "2", "0.5"
  - unit: measurement unit, e.g. "золотник", "фунт", "ведро", "штоф", "бутылка", "стакан"
  - original: the ingredient phrase as written in the original text

Return a JSON object with a single key "recipes" whose value is an ARRAY of recipe objects \
(one element per recipe). Example shape: {"recipes": [ {"name": ...}, {"name": ...} ]}.
Important: preserve the EXACT original text of each recipe. Do not modify, correct, or modernize it."""


def _extract_recipes(result) -> list[dict]:
    """Normalize an LLM JSON response to a list of recipe dicts.

    Handles bare arrays, {"recipes": [...]} wrappers, a single recipe object,
    and other single list-valued fields (models differ under json_object mode).
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("recipes", "result", "results", "data"):
            val = result.get(key)
            if isinstance(val, list):
                return val
        if any(k in result for k in ("name", "ingredients", "original_text")):
            return [result]
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


async def extract_recipes_from_section(
    section_text: str,
    book_title: str = "",
    recipe_pattern: str | None = None,
    progress_callback=None,
) -> list[ExtractedRecipe]:
    """Extract structured recipes from a recipe section using LLM.

    If the section is too large for a single call, splits it and processes in parts.
    """
    cb = progress_callback or (lambda msg: None)

    if len(section_text) <= MAX_CHARS_PER_CALL:
        cb(f"Single call: {len(section_text)} chars")
        return await _extract_single(section_text, book_title, recipe_pattern)

    # Split large sections
    all_recipes = []
    lines = section_text.split("\n")
    chunk_lines = []
    chunk_len = 0
    chunk_num = 0

    for line in lines:
        chunk_lines.append(line)
        chunk_len += len(line) + 1
        if chunk_len >= MAX_CHARS_PER_CALL * 0.8:
            chunk_num += 1
            chunk_text = "\n".join(chunk_lines)
            cb(f"Extraction chunk {chunk_num}: {len(chunk_text)} chars")
            recipes = await _extract_single(chunk_text, book_title, recipe_pattern)
            cb(f"Extraction chunk {chunk_num}: got {len(recipes)} recipes")
            all_recipes.extend(recipes)
            chunk_lines = []
            chunk_len = 0

    if chunk_lines:
        chunk_num += 1
        chunk_text = "\n".join(chunk_lines)
        cb(f"Extraction chunk {chunk_num} (last): {len(chunk_text)} chars")
        recipes = await _extract_single(chunk_text, book_title, recipe_pattern)
        cb(f"Extraction chunk {chunk_num}: got {len(recipes)} recipes")
        all_recipes.extend(recipes)

    cb(f"Total extracted: {len(all_recipes)} recipes")
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

    recipes = _extract_recipes(result)
    return [
        _parse_recipe(r) for r in recipes
        if _is_valid_recipe(r) and _is_grounded(r.get("original_text", ""), text)
    ]


def _norm_for_match(t: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", (t or "").lower(), flags=re.UNICODE).strip() + " "


def _is_grounded(original_text: str, source_text: str, shingle: int = 6) -> bool:
    """True if the recipe text is traceable to the source chunk it came from.

    Backstop against the LLM fabricating recipes from its own knowledge: a
    genuine extraction copies original_text out of the section, so at least one
    short word-shingle must appear verbatim in the source. Fabricated recipes
    (e.g. invented archaic tinctures with no counterpart in a modern book) share
    no contiguous run with the source and get dropped.
    """
    words = _norm_for_match(original_text).split()
    if not words:
        return False
    src = _norm_for_match(source_text)
    k = min(shingle, len(words))
    return any(
        " " + " ".join(words[i:i + k]) + " " in src
        for i in range(0, len(words) - k + 1)
    )


def _is_valid_recipe(data: dict) -> bool:
    """Check if extracted data looks like a real recipe.

    Rejects bare section headers and tiny fragments: a genuine recipe (prose or
    structured) carries a preparation process, which never fits in <150 chars.
    A short body with no ingredients is almost certainly a misclassified header
    (Оборудование / Сырьё / Пропорции).
    """
    name = data.get("name", "").strip()
    if not name or len(name) < 3:
        return False
    text = (data.get("original_text") or "").strip()
    if len(text) < MIN_RECIPE_TEXT_LEN:
        return False
    # Content guard against species descriptions (fungus/plant determiners): a
    # candidate with NO parseable ingredients AND no preparation verb is a
    # description with an edibility note, not a recipe. Genuine prose recipes keep
    # passing because they always carry a process verb (настоять/отварить/…).
    ingredients = data.get("ingredients") or []
    if not ingredients and not _PREP_VERB_RE.search(text):
        return False
    return True


def _normalize_category(raw: str) -> str:
    """Map an LLM-returned category to the whitelist; unknown -> 'другое'."""
    cat = (raw or "").strip().lower()
    return cat if cat in VALID_CATEGORIES else "другое"


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
        category=_normalize_category(data.get("category", "другое")),
        original_text=data.get("original_text", ""),
        ingredients=ingredients,
    )
