"""Bridge the recipe and herbalism domains by name-matching recipe ingredients
to herbarium plants.

Matching is name-based and intentionally considers ALL alternative names on
both sides:

  * plant:      ``name``, ``name_latin``, ``names_historical``
  * ingredient: recipe ``name``, ``original_name``, canonical ``Ingredient``
                name and its ``IngredientSynonym`` rows

Phase-1 strategy — two conservative tiers (a wrong link is worse than a missing
one):

  1. **exact** match on a normalized full-name string;
  2. **stem-token-subset** match — every identifying word of a plant name is
     present among the ingredient's words, after dropping plant-"part" words
     ("трава", "корень", "цвет", …) and crude Russian case-ending stemming so
     "корень валерианы" resolves to the plant "валериана".

Single short tokens (< 4 chars after stemming, e.g. "мак", "лук") only ever
match exactly — never through the subset tier — to avoid spurious links.
"""

import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.models.plant import Plant

# Words naming a *part* of a plant, not its identity — stripped before matching.
# Listed in surface forms so they are dropped before stemming.
_PART_WORDS = {
    "трава", "травы", "трав", "травка",
    "корень", "корня", "корни", "корней", "корневище", "корневища",
    "лист", "листья", "листьев", "листа", "листьев", "листики",
    "цвет", "цветы", "цветок", "цветков", "цветки", "цветочки", "соцветие", "соцветия",
    "плод", "плоды", "плодов", "плода",
    "семя", "семена", "семян", "семечки",
    "кора", "коры", "корка",
    "сок", "соки",
    "ягода", "ягоды", "ягод",
    "почки", "почка", "почек",
    "побеги", "побег", "побегов",
    "веточки", "ветки", "ветка",
    "масло", "настойка", "настой", "отвар", "экстракт", "вытяжка",
    "сушеный", "сушеная", "свежий", "свежая",
}

# Prepositions / conjunctions that leak in from verbatim recipe phrases.
_STOPWORDS = {
    "по", "на", "для", "из", "от", "до", "и", "с", "со", "в", "во",
    "за", "к", "о", "об", "а", "но", "же", "ли", "или", "да",
}

_VOWEL_ENDINGS = "аяоеёыиуюьйъ"
_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_MIN_STEM = 3            # never stem a token below this length
_MIN_KEY_TOKEN = 4       # a plant noun-key must be at least this long (stemmed)

# Adjectival suffixes (full + oblique + possessive). A plant must never be
# identified by an adjective alone — folk names like "винный корень" or "малый
# василёк" would otherwise match every "винный …"/"малый …" ingredient. Noun
# heads (зверобой, валериана, копытень) don't carry these endings.
_ADJ_SUFFIXES = (
    "ный", "ная", "ное", "ные", "ным", "ной", "ную", "нее", "него", "ному",
    "кий", "кая", "кое", "кие", "кого", "кому", "ким",
    "ский", "ская", "ское", "ские", "цкий", "цкая",
    "овый", "евый", "овая", "евая", "иный", "ьный",
    "истый", "истая", "чатый", "чатая",
    "лый", "лая", "лое", "лые", "ший", "шая", "шее", "щий", "щая",
    "ова", "ева", "ина", "ына", "ьего", "ьих",
)


def _is_adjective(tok: str) -> bool:
    """Heuristic: does a (normalized, pre-stem) token look like an adjective?"""
    return len(tok) >= 5 and tok.endswith(_ADJ_SUFFIXES)


def normalize(s: str | None) -> str:
    """Lowercase, de-ё, strip punctuation, collapse whitespace."""
    if not s:
        return ""
    s = s.lower().strip().replace("ё", "е")
    s = _PUNCT_RE.sub(" ", s)
    return _SPACE_RE.sub(" ", s).strip()


def _stem(token: str) -> str:
    """Crude single-ending stemmer: drop one trailing inflection vowel/й/ь/ъ.

    Handles the common nominative↔genitive shift for plant heads
    ("валерианы"→"валериан", "зверобоя"→"зверобо", "зверобой"→"зверобо")
    without an external lemmatizer. Won't touch tokens already at min length.
    """
    if len(token) > _MIN_STEM and token[-1] in _VOWEL_ENDINGS:
        return token[:-1]
    return token


def _stem_tokens(s: str | None) -> frozenset[str]:
    """Normalized, part-word-stripped, stemmed identifying tokens of a name."""
    out: set[str] = set()
    for tok in normalize(s).split():
        # Drop part-words, prepositions, numbers, units and very short noise so a
        # verbatim recipe fragment ("по 6 золотников") can't inject stray tokens.
        if not tok or len(tok) < 3 or tok.isdigit() or tok in _PART_WORDS or tok in _STOPWORDS:
            continue
        out.add(_stem(tok))
    return frozenset(out)


def _noun_keys(s: str | None) -> set[str]:
    """Distinctive *noun* tokens of a plant name (stemmed), dropping adjectives,
    part-words, stopwords and short noise. These are the tokens by which a plant
    may be identified inside a longer ingredient phrase."""
    out: set[str] = set()
    for tok in normalize(s).split():
        if not tok or tok in _PART_WORDS or tok in _STOPWORDS or _is_adjective(tok):
            continue
        st = _stem(tok)
        if len(st) >= _MIN_KEY_TOKEN and not st.isdigit():
            out.add(st)
    return out


class PlantMatcher:
    """Resolves a set of candidate ingredient names to a plant id.

    Build once from all plants, then call :meth:`match` per ingredient. Two
    tiers, conservative by design (a wrong link is worse than a missing one):

      1. exact normalized full-name match;
      2. a distinctive *noun* token of a plant name (зверобой, валериан,
         копытен) appears among the ingredient's tokens. Adjective-only folk
         names (e.g. "винный корень" → "винный") are excluded so common
         descriptors can't mass-match unrelated ingredients.
    """

    def __init__(self, plants):
        self._exact: dict[str, uuid.UUID] = {}
        self._noun_key: dict[str, uuid.UUID] = {}
        for p in plants:
            for variant in [p.name, p.name_latin, *(p.names_historical or [])]:
                nv = normalize(variant)
                if nv:
                    self._exact.setdefault(nv, p.id)
                for key in _noun_keys(variant):
                    self._noun_key.setdefault(key, p.id)

    def match(self, names: list[str | None]) -> uuid.UUID | None:
        clean = [n for n in names if n]
        # Tier 1: exact normalized full-name match.
        for n in clean:
            nn = normalize(n)
            if nn and nn in self._exact:
                return self._exact[nn]
        # Tier 2: a distinctive plant noun token is present in the ingredient.
        ingredient_tokens: set[str] = set()
        for n in clean:
            ingredient_tokens |= _stem_tokens(n)
        # Prefer the longest matching token (most specific) for determinism.
        best: uuid.UUID | None = None
        best_len = 0
        for tok in ingredient_tokens:
            pid = self._noun_key.get(tok)
            if pid is not None and len(tok) > best_len:
                best, best_len = pid, len(tok)
        return best


async def relink_recipe_ingredients(
    db: AsyncSession,
    plants: list[Plant] | None = None,
    commit: bool = True,
) -> dict:
    """(Re)compute plant links for every canonical ingredient and recipe
    ingredient against the current plants table. Idempotent — safe to run
    repeatedly (e.g. after each new herbalism book adds plants).

    Returns counts of links established. Does not clear links that no longer
    resolve (a plant is never deleted in normal operation).
    """
    if plants is None:
        plants = (await db.execute(select(Plant))).scalars().all()
    matcher = PlantMatcher(plants)

    # Authoritative full recompute: clear existing links first so the run is
    # idempotent and self-healing (drops stale/incorrect links from earlier
    # matcher versions or partial plant data).
    await db.execute(update(RecipeIngredient).values(plant_id=None))
    await db.execute(update(Ingredient).values(plant_id=None))

    ingredients = (await db.execute(select(Ingredient))).scalars().all()
    syn_by_ing: dict[uuid.UUID, list[str]] = {}
    for s in (await db.execute(select(IngredientSynonym))).scalars().all():
        syn_by_ing.setdefault(s.ingredient_id, []).append(s.synonym)
    ing_by_id = {i.id: i for i in ingredients}

    # 1) canonical ingredients → plant (using name + synonyms)
    linked_ing = 0
    for ing in ingredients:
        pid = matcher.match([ing.canonical_name, *syn_by_ing.get(ing.id, [])])
        if pid and ing.plant_id != pid:
            ing.plant_id = pid
            linked_ing += 1

    # 2) recipe ingredients → plant (inherit canonical link, else match names)
    ris = (await db.execute(select(RecipeIngredient))).scalars().all()
    linked_ri = 0
    for ri in ris:
        ing = ing_by_id.get(ri.ingredient_id) if ri.ingredient_id else None
        if ing is not None and ing.plant_id is not None:
            if ri.plant_id != ing.plant_id:
                ri.plant_id = ing.plant_id
                linked_ri += 1
            continue
        # NB: deliberately exclude ri.original_name — it is the verbatim recipe
        # fragment (with amounts/units like "по 6 золотников") and injects noise
        # tokens that cause false matches. Match only on clean ingredient names.
        names: list[str | None] = [ri.name]
        if ing is not None:
            names.append(ing.canonical_name)
            names.extend(syn_by_ing.get(ing.id, []))
        pid = matcher.match(names)
        if pid and ri.plant_id != pid:
            ri.plant_id = pid
            linked_ri += 1

    if commit:
        await db.commit()
    return {"linked_ingredients": linked_ing, "linked_recipe_ingredients": linked_ri}
