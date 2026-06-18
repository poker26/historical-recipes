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

Very short tokens (< 3 chars after stemming) only ever match exactly — never
through the subset tier — to avoid spurious links. Genuine 3-char genera
(аир, дуб, рута, мак, лук) do seed subset keys so a bare inflected mention
("аира", "дуба") still resolves.
"""

import re
import uuid
from pathlib import Path

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recipe import RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.models.plant import (
    Plant,
    PlantProperty,
    PlantBookMention,
    PlantMedicinalUse,
    PlantCompound,
    PlantHarvest,
    PlantHabitat,
    PlantToxicity,
    PlantCulinaryUse,
    PlantCompatibility,
)

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
    "дерево", "дерева", "деревья", "деревьев", "древесина", "древесины",
    # "гриб" is shared across many mushroom names (белый гриб, польский гриб …):
    # demoted to a part/qualifier word — same fix as "дерево" — so a bare recipe
    # "грибы" never over-links to one species and distinct fungi don't collide.
    "гриб", "грибы", "гриба", "грибов", "грибу", "грибом", "грибе",
    "грибной", "грибная", "грибное", "грибные", "грибочки", "грибочек",
    "масло", "настойка", "настой", "отвар", "экстракт", "вытяжка",
    "сушеный", "сушеная", "свежий", "свежая",
}

# Prepositions / conjunctions that leak in from verbatim recipe phrases.
_STOPWORDS = {
    "по", "на", "для", "из", "от", "до", "и", "с", "со", "в", "во",
    "за", "к", "о", "об", "а", "но", "же", "ли", "или", "да",
}

# Bare common-ingredient nouns. When one of these stands ALONE as a folk /
# historical plant name it collides exactly with the spice/fruit a recipe
# actually means (Зорька's folk name "гвоздика" must not capture the clove
# spice; Змееголовник's "мелисса"; Василёк-named species), so it is never
# indexed as a sole exact key. Multi-word folk names that merely contain such
# a noun ("барская гвоздика", "земляное яблоко") remain distinctive and kept.
_AMBIGUOUS_FOLK = {
    "гвоздика", "гвоздики", "вишня", "вишни", "яблоко", "яблоки",
    "перец", "мелисса", "василек", "дерево", "ладан", "мак", "лук",
    "роза", "фиалка", "мята", "орех", "лимон", "миндаль", "корица",
    "гриб", "грибы",
}

# Universal NON-PLANT substances — water, table salt, sugar, the alcohols,
# vinegar, dairy/egg/animal products, flour/starch/yeast, and mineral / chemical
# materia medica (lime, alum, vitriol, sal-ammoniac, potash, tar, glycerin,
# camphor…). A recipe names these constantly as ordinary ingredients, but they
# are NOT botanical species and must never resolve to a herbarium card, however
# a junk folk-name alias on some card spells them ("вода"→туффах май, "соль"→
# Бабья соль, "сахар"→солодка). This is the DURABLE cure for that recurring
# mis-link: because ``relink_recipe_ingredients`` clears and recomputes every
# link through this matcher, a one-off manual unlink always got recaptured — the
# block has to live HERE, in the matcher, to stick. Stored normalized (lowercase,
# ё→е, single-spaced). Spice/food PLANTS (перец, корица, имбирь, хрен, лук…) are
# deliberately ABSENT — they are real species.
_NON_PLANT_SUBSTANCES = frozenset({
    # water · sugar · salt
    "вода", "сахар", "сахара", "сахарный песок", "сахарная пудра",
    "тростниковый сахар", "сахар тростниковый", "кристаллический сахар",
    "леденцовый сахар", "жжёный сахар", "жженый сахар",
    "соль", "соли", "поваренная соль", "соль поваренная", "морская соль",
    "каменная соль", "крупная соль", "мелкая соль", "столовая соль",
    "крупнозернистая поваренная соль", "крупнозернистая соль",
    # alcohols · wine · vinegar
    "спирт", "винный спирт", "хлебный спирт", "этиловый спирт", "чистый спирт",
    "водка", "вино", "красное вино", "белое вино", "виноградное вино",
    "уксус", "винный уксус", "яблочный уксус", "столовый уксус", "уксусная кислота",
    # dairy · egg · animal products
    "мед", "молоко", "козье молоко", "молоко козье", "коровье молоко", "сливки",
    "сметана", "творог", "масло коровье", "сливочное масло", "сало", "свиное сало",
    "яйцо", "яйца", "желток", "яичный желток", "белок", "яичный белок",
    "воск", "пчелиный воск", "мыло",
    # flour · starch · yeast
    "мука", "ржаная мука", "пшеничная мука", "мука ржаная", "крахмал", "дрожжи",
    # minerals · chemical materia medica
    "зола", "известь", "негашеная известь", "гашеная известь", "квасцы",
    "купорос", "медный купорос", "железный купорос", "нашатырь", "нашатырный спирт",
    "сода", "поташ", "мел", "гипс", "глина", "песок", "селитра",
    "деготь", "березовый деготь", "скипидар", "глицерин", "растительный глицерин",
    "камфора", "желатин",
})

_VOWEL_ENDINGS = "аяоеёыиуюьйъ"
_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_MIN_STEM = 3            # never stem a token below this length
_MIN_KEY_TOKEN = 3       # a plant noun-key must be at least this long (stemmed):
                         # 3 admits real short genera (аир, дуб, липа, рута, мак,
                         # лук, мята) so a bare inflected mention ("аира") links;
                         # 2-char tokens stay out as noise.

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


# Latin binomial key — genus + species epithet, author-citation- and
# case-insensitive. A scientific name is "Genus species Author[, infraspecific]";
# the AUTHOR citation ("L." = Linnaeus, "DC.", "(L.) Ach.", "Mill." …) and the
# hybrid marker "×" are NOT part of the species identity, so two rows whose latin
# names agree on genus+species but differ only in author/case ("gratiola
# officinalis" vs "Gratiola officinalis L.") denote the SAME species and should
# be one plant. Returns None when there is no genus+species pair to key on.
_LATIN_KEY_RE = re.compile(r"[^a-z\s]")


def _latin_key(name_latin: str | None) -> str | None:
    """Genus+species dedup key from a latin name (lowercased, author-stripped).

    Lowercase → drop the hybrid "×" and every non-[a-z] character (digits,
    dots, parens, the author's accented letters) → take the first two surviving
    word tokens (genus + species epithet). A standalone "x" (ascii hybrid mark)
    is dropped. Fewer than two tokens (a bare genus, or junk) yields None so it
    never collapses unrelated rows.
    """
    if not name_latin:
        return None
    s = name_latin.lower().replace("×", " ")
    s = _LATIN_KEY_RE.sub(" ", s)
    toks = [t for t in s.split() if t != "x"]
    if len(toks) < 2:
        return None
    return f"{toks[0]} {toks[1]}"


async def resolve_latin_to_plants(
    db: AsyncSession,
    latin_names: list[str],
) -> dict[str, Plant | None]:
    """Map each incoming latin binomial to a herbarium ``Plant`` via the same
    genus+species :func:`_latin_key` used for herbarium dedup, so an engine result
    like "Gratiola officinalis L." matches our row stored as "Gratiola officinalis".

    Returns ``{input_latin: Plant | None}`` (None = no taxon in our corpus). Builds
    the key→plant index once from all plants with a latin name; when two of our rows
    share a key (shouldn't after dedupe) the richest-named one wins deterministically.
    """
    rows = (await db.execute(select(Plant).where(Plant.name_latin.isnot(None)))).scalars().all()
    index: dict[str, Plant] = {}
    for p in rows:
        key = _latin_key(p.name_latin)
        if not key:
            continue
        cur = index.get(key)
        if cur is None or _latin_token_count(p.name_latin) > _latin_token_count(cur.name_latin):
            index[key] = p
    out: dict[str, Plant | None] = {}
    for name in latin_names:
        out[name] = index.get(_latin_key(name) or "")
    return out


def _stem(token: str) -> str:
    """Crude single-ending stemmer: drop one trailing inflection vowel/й/ь/ъ.

    Handles the common nominative↔genitive shift for plant heads
    ("валерианы"→"валериан", "зверобоя"→"зверобо", "зверобой"→"зверобо")
    without an external lemmatizer. Won't touch tokens already at min length.
    """
    if len(token) > _MIN_STEM and token[-1] in _VOWEL_ENDINGS:
        return token[:-1]
    return token


# Stems of the single-word substances, so the loose noun-token tier (and key
# building) also drop them — e.g. "Бабья соль" must not seed the key "сол" that
# would capture every "соль". Verified safe: no real plant's stemmed noun-key
# equals any of these (real species roots are longer — солодк, солянк, водяник…).
_NON_PLANT_STEMS = frozenset(
    _stem(w) for w in _NON_PLANT_SUBSTANCES if " " not in w
)


# Relational ("denominal") adjective endings: X-ов-/X-ев- = "made of / from X".
# A recipe names a plant material this way ("лавандовые цветы", "розовое масло",
# "анисовых семян") instead of the bare noun, so reducing the adjective to its
# base noun root (лавандовые->лаванд, розовое->роз) lets it match the plant's
# noun key. Longest-first so the linking morph -ов/-ев is stripped with the
# inflection in one pass.
_DENOMINAL_ENDINGS = tuple(sorted((
    "овые", "евые", "овый", "евый", "овая", "евая", "овое", "евое",
    "овых", "евых", "овым", "евым", "овом", "евом", "ового", "евого",
    "овому", "евому", "овую", "евую", "овой", "евой", "ова", "ева",
), key=len, reverse=True))


# Denominal roots whose relational adjective refers to a DIFFERENT plant than
# the noun they resemble — so the X-овый form must NOT seed that noun's key.
# "фиалковый корень" is orris root (Iris/Касатик), named for its violet SCENT,
# and has nothing to do with Фиалка (Viola); the bare noun "фиалка" still links
# Фиалка normally via the ordinary noun path.
_DENOMINAL_BLOCK = {"фиалк"}


def _denominal_root(tok: str) -> str | None:
    """Base noun root of an X-ов-/X-ев- relational adjective, else None.

    Only the -ов-/-ев- class (unambiguously "of/from <noun>"); the root is
    emitted as an EXTRA candidate token, so it links only when it exactly equals
    an existing plant's noun key — a spurious root with no plant simply no-ops.
    """
    for end in _DENOMINAL_ENDINGS:
        if tok.endswith(end) and len(tok) - len(end) >= 3:
            root = tok[: -len(end)]
            return None if root in _DENOMINAL_BLOCK else root
    return None


def _stem_tokens(s: str | None) -> frozenset[str]:
    """Normalized, part-word-stripped, stemmed identifying tokens of a name."""
    out: set[str] = set()
    for tok in normalize(s).split():
        # Drop part-words, prepositions, numbers, units and very short noise so a
        # verbatim recipe fragment ("по 6 золотников") can't inject stray tokens.
        if not tok or len(tok) < 3 or tok.isdigit() or tok in _PART_WORDS or tok in _STOPWORDS:
            continue
        out.add(_stem(tok))
        root = _denominal_root(tok)
        if root and len(root) >= 3:
            out.add(root)
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


# Curated alias knowledge (see plant_aliases.txt for the format and rationale).
# Edited by hand, version-controlled, never written by extraction.
_ALIASES_PATH = Path(__file__).resolve().parent / "plant_aliases.txt"


def load_plant_aliases(path: Path = _ALIASES_PATH) -> dict[str, list[str]]:
    """Parse the curated ``<canonical> = alias1, alias2, ...`` knowledge file.

    Returns ``{canonical_name: [alias, ...]}`` with surface forms verbatim
    (normalization happens at use-site). Missing/unparseable lines are skipped
    so a malformed entry never breaks matching. A missing file yields ``{}``.
    """
    out: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        canon, _, rhs = line.partition("=")
        canon = canon.strip()
        aliases = [a.strip() for a in rhs.split(",") if a.strip()]
        if canon and aliases:
            out.setdefault(canon, []).extend(aliases)
    return out


def _adj_stem(tok: str) -> str:
    """Strip the longest matching adjectival ending so inflected forms of the
    same epithet collapse: пятилопастной / пятилопастный -> пятилопаст."""
    for suf in sorted(_ADJ_SUFFIXES, key=len, reverse=True):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def _identity(name: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Split a plant name into (genus/distinctive nouns, species-epithet stems).

    Nouns are stemmed and kept only above ``_MIN_KEY_TOKEN`` so a bare common
    noun (лук, мак) never seeds an identity. Adjectives are reduced to their
    epithet stem so inflection variants of the same species match. Together the
    two sets identify a plant precisely enough to dedup the herbarium without
    collapsing two genuinely different species of the same genus.
    """
    nouns: set[str] = set()
    adjs: set[str] = set()
    for tok in normalize(name).split():
        if not tok or tok in _PART_WORDS or tok in _STOPWORDS or tok.isdigit():
            continue
        if _is_adjective(tok):
            a = _adj_stem(tok)
            if len(a) >= 3:
                adjs.add(a)
        else:
            st = _stem(tok)
            if len(st) >= _MIN_KEY_TOKEN:
                nouns.add(st)
    return frozenset(nouns), frozenset(adjs)


def same_plant_identity(a: str | None, b: str | None) -> bool:
    """True if two plant names denote the SAME species — conservatively.

    Requires a shared genus (one name's noun set is a subset of the other, so a
    bare genus matches "genus + epithet") AND compatible epithets (one adjective
    set a subset of the other, so an inflected variant matches but a *different*
    species epithet does not). A name with no distinctive noun (e.g. bare "Лук")
    never matches, so it stays a separate stub rather than risk a false merge.
    """
    na, aa = _identity(a)
    nb, ab = _identity(b)
    if not na or not nb:
        return False
    if not (na <= nb or nb <= na):
        return False
    return aa <= ab or ab <= aa


def is_more_specific(a: str | None, b: str | None) -> bool:
    """True if name ``a`` is a strictly richer form of the same plant as ``b``
    (same genus, ``b``'s epithets a subset of ``a``'s, and ``a`` carries more
    identifying tokens) — used to promote a bare stub name to the fuller one."""
    na, aa = _identity(a)
    nb, ab = _identity(b)
    if not na or not nb:
        return False
    return nb <= na and ab <= aa and (len(na) + len(aa)) > (len(nb) + len(ab))


class PlantMatcher:
    """Resolves a set of candidate ingredient names to a plant id.

    Build once from all plants, then call :meth:`match` per ingredient. Two
    tiers, conservative by design (a wrong link is worse than a missing one):

      1. exact normalized full-name match — across ALL names (primary, latin,
         historical/folk). A folk name like "винный корень" matches only when
         the ingredient reproduces it verbatim, which is rare and safe.
      2. a distinctive *noun* token of the plant's **primary** botanical name
         (зверобой, валериан, копытен) appears among the ingredient's tokens.

    The loose token tier intentionally does NOT draw keys from historical/folk
    names: those are heavily metaphorical common nouns (гвоздика=Зорька,
    вишня=белладонна, яблоко=кирказон, перец=копытень, мелисса=змееголовник)
    that would mass-match the real spice/fruit ingredients meant in recipes.
    Folk names therefore only ever match as complete exact strings (tier 1).

    On top of the plant data, a curated knowledge file (plant_aliases.txt next
    to this module) injects hand-vetted alternative names that aren't reliably
    in the source books (e.g. "кишнец"→Кориандр). Because they're vetted and
    distinctive, curated aliases feed both tiers (exact AND noun-token).
    """

    def __init__(self, plants):
        self._exact: dict[str, uuid.UUID] = {}
        self._noun_key: dict[str, uuid.UUID] = {}
        for p in plants:
            # Tier 1 keys, scientific identity: primary + latin name as full
            # strings (a recipe is free to name a plant by either). A bare
            # non-plant substance as a primary name (a junk «Соль»/«Мёд» card)
            # is never indexed — it isn't a species.
            for variant in (p.name, p.name_latin):
                nv = normalize(variant)
                if nv and nv not in _NON_PLANT_SUBSTANCES:
                    self._exact.setdefault(nv, p.id)
            # Tier 1 keys, folk names: kept as full strings, but a single bare
            # common-ingredient noun (гвоздика, мелисса, …) or any universal
            # non-plant substance (соли, вода, сахар — the recurring landmine)
            # is dropped so it can't capture the real spice/substance in recipes.
            for variant in (p.names_historical or []):
                nv = normalize(variant)
                if not nv or nv in _NON_PLANT_SUBSTANCES or (" " not in nv and nv in _AMBIGUOUS_FOLK):
                    continue
                self._exact.setdefault(nv, p.id)
            # Tier 2 keys: noun tokens of the PRIMARY name only (folk names
            # excluded — see class docstring). Substance stems (сол, вод, …) are
            # never seeded, so a card like «Бабья соль» can't capture «соль».
            for key in _noun_keys(p.name):
                if key not in _NON_PLANT_STEMS:
                    self._noun_key.setdefault(key, p.id)

        # Curated knowledge: fold hand-maintained aliases into both tiers. These
        # are vetted, distinctive names (e.g. "кишнец"→Кориандр), so unlike
        # auto-extracted folk names they ARE allowed to seed noun-token keys.
        self._merge_aliases(load_plant_aliases())

    def _resolve_canonical(self, canon: str) -> uuid.UUID | None:
        """Find the plant a curated alias line points at: by exact full name
        (primary/latin/historical), else by a single distinctive noun token."""
        nv = normalize(canon)
        if not nv:
            return None
        if nv in self._exact:
            return self._exact[nv]
        keys = _noun_keys(canon)
        if len(keys) == 1:
            return self._noun_key.get(next(iter(keys)))
        return None

    def _merge_aliases(self, aliases: dict[str, list[str]]) -> None:
        for canon, names in aliases.items():
            pid = self._resolve_canonical(canon)
            if pid is None:
                continue  # target plant not in the herbarium yet — activates later
            for alias in names:
                nv = normalize(alias)
                if nv and nv not in _NON_PLANT_SUBSTANCES:
                    self._exact.setdefault(nv, pid)
                for key in _noun_keys(alias):
                    if key not in _NON_PLANT_STEMS:
                        self._noun_key.setdefault(key, pid)

    def match(self, names: list[str | None]) -> uuid.UUID | None:
        # Universal non-plant substances (water, salt, sugar, alcohols, dairy,
        # minerals…) are ingredients, not species — never resolve them to a
        # plant card, whatever a junk folk alias calls them. Dropping them here
        # AND excluding their keys/stems above makes the block survive a relink.
        clean = [n for n in names if n and normalize(n) not in _NON_PLANT_SUBSTANCES]
        # Tier 1: exact normalized full-name match.
        for n in clean:
            nn = normalize(n)
            if nn and nn in self._exact:
                return self._exact[nn]
        # Tier 2: a distinctive plant noun token is present in the ingredient.
        ingredient_tokens: set[str] = set()
        for n in clean:
            ingredient_tokens |= _stem_tokens(n)
        ingredient_tokens -= _NON_PLANT_STEMS
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
    progress=None,
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
    for i, ri in enumerate(ris):
        if progress is not None and i % 1000 == 0:
            progress(i, len(ris), linked_ri)
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


# Child tables whose ``plant_id`` FK is repointed from a losing row to the
# survivor when two duplicate plants merge. PlantCompatibility (plant_a/plant_b)
# and the cross-domain RecipeIngredient/Ingredient links are handled separately.
_PLANT_CHILD_MODELS = (
    PlantProperty, PlantBookMention, PlantMedicinalUse, PlantCompound,
    PlantHarvest, PlantHabitat, PlantToxicity, PlantCulinaryUse,
)


def _latin_token_count(name_latin: str | None) -> int:
    """How many latin word tokens a name carries (author citation included).

    Used as a survivor tie-break: a name WITH the author ("Gratiola officinalis
    L." → 3) is the more canonical/complete binomial than the bare "Gratiola
    officinalis" (→ 2), so it wins as the merged row's ``name_latin``.
    """
    if not name_latin:
        return 0
    s = name_latin.lower().replace("×", " ")
    s = _LATIN_KEY_RE.sub(" ", s)
    return len([t for t in s.split() if t != "x"])


async def _plant_fact_counts(db: AsyncSession, plant_ids: list[uuid.UUID]) -> dict:
    """Total child-fact rows per plant, summed across every fact table.

    Drives survivor selection: the row carrying the most accumulated knowledge
    is kept, and the thinner duplicates are folded into it.
    """
    counts: dict[uuid.UUID, int] = {pid: 0 for pid in plant_ids}
    if not plant_ids:
        return counts
    for model in _PLANT_CHILD_MODELS:
        rows = (await db.execute(
            select(model.plant_id, func.count())
            .where(model.plant_id.in_(plant_ids))
            .group_by(model.plant_id)
        )).all()
        for pid, n in rows:
            counts[pid] = counts.get(pid, 0) + n
    return counts


async def merge_plants_by_latin_key(
    db: AsyncSession,
    dry_run: bool = True,
    commit: bool = True,
) -> dict:
    """Deduplicate the herbarium by latin binomial (genus + species epithet).

    Two ``Plant`` rows whose ``name_latin`` agree on genus+species after
    stripping the author citation and case ("gratiola officinalis" vs "Gratiola
    officinalis L.") are the SAME species recorded twice — e.g. a recipe book
    created a stub before a determiner added the full monograph. This groups all
    rows by :func:`_latin_key`, keeps the richest row in each group, repoints
    every child fact and cross-domain link onto it, unions the identity scalars,
    and deletes the losers.

    With ``dry_run`` (default) nothing is written — it returns the plan so the
    scale of the merge can be reviewed first. The list of deleted plant ids is
    returned so the caller can purge their now-orphaned ``plants_v2`` points.
    """
    plants = (await db.execute(select(Plant))).scalars().all()
    groups: dict[str, list[Plant]] = {}
    for p in plants:
        key = _latin_key(p.name_latin)
        if key:
            groups.setdefault(key, []).append(p)
    dup_groups = {k: ps for k, ps in groups.items() if len(ps) > 1}

    all_ids = [p.id for ps in dup_groups.values() for p in ps]
    counts = await _plant_fact_counts(db, all_ids)

    plan: list[dict] = []
    deleted_ids: list[str] = []

    def _rank(p: Plant) -> tuple:
        # richest by facts → most complete latin name → longer latin string →
        # stable by name for determinism.
        return (
            counts.get(p.id, 0),
            _latin_token_count(p.name_latin),
            len(p.name_latin or ""),
            p.name or "",
        )

    for key, ps in sorted(dup_groups.items()):
        survivor = max(ps, key=_rank)
        losers = [p for p in ps if p.id != survivor.id]

        # Best (most complete) latin name across the group becomes canonical.
        best_latin = max(ps, key=lambda p: (_latin_token_count(p.name_latin), len(p.name_latin or "")))
        canonical_latin = best_latin.name_latin or survivor.name_latin

        plan.append({
            "latin_key": key,
            "survivor": {"id": str(survivor.id), "name": survivor.name, "name_latin": survivor.name_latin},
            "merged": [{"id": str(l.id), "name": l.name, "name_latin": l.name_latin} for l in losers],
        })

        if dry_run:
            continue

        loser_ids = [l.id for l in losers]
        # Repoint every plant-scoped fact table.
        for model in _PLANT_CHILD_MODELS:
            await db.execute(
                update(model).where(model.plant_id.in_(loser_ids)).values(plant_id=survivor.id)
            )
        # Compatibility edges reference plants on both ends.
        await db.execute(
            update(PlantCompatibility).where(PlantCompatibility.plant_a_id.in_(loser_ids)).values(plant_a_id=survivor.id)
        )
        await db.execute(
            update(PlantCompatibility).where(PlantCompatibility.plant_b_id.in_(loser_ids)).values(plant_b_id=survivor.id)
        )
        # Cross-domain links (recipes ↔ plants).
        await db.execute(
            update(RecipeIngredient).where(RecipeIngredient.plant_id.in_(loser_ids)).values(plant_id=survivor.id)
        )
        await db.execute(
            update(Ingredient).where(Ingredient.plant_id.in_(loser_ids)).values(plant_id=survivor.id)
        )

        # Merge identity onto the survivor: prefer the most complete latin name,
        # fill empty scalars from losers, union the multi-valued fields, and keep
        # each losing row's distinct Russian name as a historical synonym.
        survivor.name_latin = canonical_latin
        hist: list[str] = list(survivor.names_historical or [])
        parts: list[str] = list(survivor.parts_used or [])
        for l in losers:
            for h in (l.names_historical or []):
                if h and h not in hist:
                    hist.append(h)
            if l.name and l.name != survivor.name and l.name not in hist:
                hist.append(l.name)
            for pt in (l.parts_used or []):
                if pt and pt not in parts:
                    parts.append(pt)
            survivor.family = survivor.family or l.family
            survivor.family_latin = survivor.family_latin or l.family_latin
            survivor.description = survivor.description or l.description
            if l.is_toxic:
                survivor.is_toxic = True
            if (survivor.kingdom or "растение") == "растение" and (l.kingdom or "") == "гриб":
                survivor.kingdom = "гриб"
        survivor.names_historical = hist or None
        survivor.parts_used = parts or None

        for l in losers:
            if l.qdrant_point_id or l.qdrant_collection:
                deleted_ids.append(str(l.id))
            await db.delete(l)

    if not dry_run and commit:
        await db.commit()

    return {
        "dry_run": dry_run,
        "groups": len(dup_groups),
        "plants_merged": sum(len(g["merged"]) for g in plan),
        "deleted_qdrant_ids": deleted_ids,
        "plan": plan,
    }
