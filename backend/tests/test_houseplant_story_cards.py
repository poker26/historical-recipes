"""Сборка рассказа о растении в карточку."""

from types import SimpleNamespace

from app.services.houseplant_story_cards import (
    _scope, apply_story, build_story, clean_verdict,
)


def row(latin, kind, fact, ru=None, book="Жизнь растений. Том 6", year=1982, page=475):
    return SimpleNamespace(taxon_latin=latin, taxon_ru=ru, kind=kind, fact=fact,
                           book=book, year=year, page=page)


def test_scope_rules():
    assert _scope("Monstera deliciosa", "Monstera deliciosa") == 0
    assert _scope("Monstera deliciosa", "Monstera") == 1
    assert _scope("Monstera", "Monstera deliciosa") == 2
    assert _scope("Monstera deliciosa", "Monstera adansonii") is None   # соседний вид
    assert _scope("Monstera deliciosa", "Philodendron") is None
    assert _scope("Monstera", None) is None


ROWS = [
    row("Monstera", "name", "Название рода происходит от латинского слова «причудливый» и дано за листья с отверстиями."),
    row("Monstera", "homeland", "Род включает около 25 видов лиан Центральной и Южной Америки."),
    row("Monstera deliciosa", "danger", "Недозревшие плоды обжигают рот игольчатыми кристаллами.", ru="Монстера деликатесная"),
    row("Monstera adansonii", "biology", "Факт о соседнем виде, которого в карточке вида быть не должно."),
    row("Monstera deliciosa", "biology", "Во время мужской фазы цветения початок нагревается на 15 градусов выше воздуха.",
        ru="Монстера деликатесная"),
]


def test_species_card_gets_own_and_genus_facts_only():
    story = build_story("Monstera deliciosa", "Монстера деликатесная", ROWS)
    texts = [f["text"] for b in story["story"] for f in b["facts"]]
    assert not any("соседнем" in t for t in texts)
    assert story["facts"] == 4
    assert story["lead"]["text"].startswith("Во время мужской фазы")       # biology раньше name
    assert story["lead"]["source"] == "Жизнь растений. Том 6, 1982"
    assert story["homeland"].startswith("Род включает")


def test_genus_card_labels_species_facts():
    story = build_story("Monstera", "Монстера", ROWS)
    danger = next(b for b in story["story"] if b["kind"] == "danger")
    assert danger["facts"][0]["about"] == "Монстера деликатесная"


def test_verdict_loses_boilerplate_and_gains_homeland():
    old = ("Растение ядовито при попадании внутрь. Комнатное растение. Здесь собрано то, что о нём "
           "пишут книги по комнатному цветоводству: свет, полив, земля, пересадка и размножение. "
           "Книги описывают весь род, поэтому совет годится и для сортов.")
    assert clean_verdict(old, "Растёт в Южной Америке.") == "Растение ядовито при попадании внутрь. Растёт в Южной Америке."
    assert clean_verdict(None, None) is None


def test_apply_story_keeps_herbarium_lead():
    story = build_story("Monstera deliciosa", "Монстера", ROWS)
    herb = {"origin": "herbarium", "lead_fact": {"text": "из травника"}, "verdict": "Съедобно."}
    out = apply_story(herb, story)
    assert out["lead_fact"]["text"] == "из травника"
    assert out["verdict"] == "Съедобно."
    assert out["story"]
    house = apply_story({"origin": "houseplant", "lead_fact": {"text": "Поливайте умеренно."}}, story)
    assert house["lead_fact"]["text"].startswith("Во время мужской фазы")
    assert house["verdict"].startswith("Род включает")


def test_herbarium_card_of_houseplant_gets_story_lead():
    story = build_story("Monstera deliciosa", "Монстера", ROWS)
    herb = {"origin": "herbarium", "lead_fact": {"text": "обогащает помещение аэроионами"}}
    out = apply_story(herb, story, houseplant=True)
    assert out["lead_fact"]["text"].startswith("Во время мужской фазы")
