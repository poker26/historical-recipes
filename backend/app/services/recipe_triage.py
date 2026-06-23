# -*- coding: utf-8 -*-
"""Recipe triage — classify each recipe so the product can surface only the real,
home-doable ones («Что приготовить из этого растения») and demote the junk.

The corpus (53k) is grounded (verbatim original_text) but the «другое» bucket (7.6k) is a
grab-bag: real recipes (вино/желе/маски) mixed with monographs-as-recipe («в народной
медицине применяют…»), industrial/lab procedures («Нейтрализация патоки фосфорной кислотой»,
«Консервирование дрожжей»), and fragments («При ларингите»). Rules over original_text + name
+ category + ingredient count (no LLM): is it a REAL recipe (quantities + prep steps), is it
HOME-DOABLE (not industrial/lab), and what KIND. `classify_recipe(...) -> dict`.
"""
import re

_QTY = re.compile(
    r"(\d+[.,]?\d*\s*(?:г|гр|кг|мг|мл|л|%|стакан|ложк|щепот|горст|капел|капл|драхм|золотник|унц|фунт|част)"
    r"|\b(?:ст|ч)\.?\s*л\.?|\bпо\s+\d|столов\w*\s+ложк|чайн\w*\s+ложк)", re.I)
_PREP = re.compile(
    r"(смеш|залить|залива|залей|настаива|настоя|настой\b|варить|кипят|добав|растер|растоп|измельч|"
    r"процед|перемеш|берут\b|требуется|приготовл|нарез|выжать|отжать|залить|настойку|отвар\w*\s+гото|"
    r"заваривать|запарить|томят|упарить|растворить)", re.I)
_INDUSTRIAL = re.compile(
    r"(перегонк|перегонят|дистилляц|дистиллят|нейтрализ|фосфорн\w*\s+кислот|сернокисл|соляной кислот|"
    r"консервиров|промышленн|в жестяных банк|автоклав|центрифуг|вакуум-аппарат|на заводе|фабричн)", re.I)
_MONOGRAPH = re.compile(
    r"(в народной медицине\s+\w+\s+(?:применя|использу)|применяют при|применяется при|"
    r"обладает\s+\w+\s+(?:действием|свойств)|используется как\s+средство|издавна\s+(?:применя|использу)|"
    r"в научной медицине|по данным\s+\w+|растение\s+содержит)", re.I)
# «Растения и грибы Сибири» structured-dosing shape: «плоды [действия] [МКБ-коды] (настой 1:20 —
# по 50 мл)». An ICD-10 code in parens — «(Е50-Е64)», «(К71)», «(J00-J47)» — never appears in a
# real cookbook recipe, so it cleanly tags a dosing reference (not a «cook this» recipe). 486 rows.
_DOSING_DUMP = re.compile(r"\(\s*[А-ЯЁA-Z]\d\d")

_FOOD = ("вино", "ликёр", "ликер", "наливк", "настойк ягод", "варень", "желе", "напиток", "салат",
         "суп", "заготовк", "квас", "компот", "пастил", "цукат", "мармелад", "конфет", "пюре",
         "морс", "кисел", "сироп", "уксус", "приправ", "соус")
_COSMETIC = ("маск", "крем", "для кожи", "для волос", "косметич", "мыло", "лосьон", "для лица",
             "скраб", "бальзам для", "для рук", "для ногтей", "шампун", "тоник для")
_MEDICINAL_CAT = {"настой", "отвар", "настойка", "мазь", "сбор", "чай", "припарка", "примочка",
                  "компресс", "ванна", "ванны", "капли", "порошок", "эликсир", "бальзам", "паста",
                  "сок", "масло", "водка", "эссенция"}


def classify_recipe(name: str | None, category: str | None,
                    original_text: str | None, n_ingredients: int = 0) -> dict:
    """Returns {kind, is_recipe, home_doable, procedure_score}.
    kind ∈ medicinal | food | cosmetic | industrial | monograph | fragment | other.
    procedure_score (0-2) = has_quantity + has_prep_verb: 2 = a real step-by-step recipe
    («залить стаканом кипятка, настоять»), 0-1 = a dosing/action data-dump (e.g. the
    «Растения и грибы Сибири» «плоды [действия] [МКБ] (настой 1:20 — по 50 мл)» shape).
    Used to rank «что приготовить» so the appealing, do-able recipes lead."""
    t = (original_text or "").strip()
    tl = t.lower()
    nm = (name or "").strip().lower()
    cat = (category or "").strip().lower()

    has_qty = bool(_QTY.search(tl))
    has_prep = bool(_PREP.search(tl))
    # a dosing reference (МКБ-coded action dump) is not a step-by-step recipe → score 0 so it
    # ranks below real recipes and isn't badged step_by_step (kept, not dropped — real dosing).
    procedure_score = 0 if _DOSING_DUMP.search(t) else int(has_qty) + int(has_prep)
    industrial = bool(_INDUSTRIAL.search(tl) or _INDUSTRIAL.search(nm))

    # is it a REAL recipe? quantities OR prep verbs, with some substance.
    is_recipe = (has_qty or has_prep) and (n_ingredients >= 1 or has_qty)
    # monograph/description masquerading as a recipe (no real procedure)
    if _MONOGRAPH.search(tl) and not (has_qty and has_prep):
        return {"kind": "monograph", "is_recipe": False, "home_doable": False, "procedure_score": procedure_score}
    # fragment: no content + no ingredients
    if len(t) < 40 and n_ingredients == 0 and not has_qty:
        return {"kind": "fragment", "is_recipe": False, "home_doable": False, "procedure_score": procedure_score}

    if industrial:
        kind = "industrial"
    elif any(w in nm or w in cat for w in _COSMETIC):
        kind = "cosmetic"
    elif any(w in nm for w in _FOOD) or cat in _FOOD or cat in ("ликёр", "напиток", "салат", "суп",
                                                                "варенье", "заготовка", "водка"):
        kind = "food"
    elif cat in _MEDICINAL_CAT:
        kind = "medicinal"
    elif is_recipe:
        kind = "other"           # a real recipe we can't type — keep, home-doable
    else:
        kind = "fragment"

    home_doable = is_recipe and not industrial
    return {"kind": kind, "is_recipe": is_recipe, "home_doable": home_doable,
            "procedure_score": procedure_score}
