"""Проверки поиска пар-дублей на живых карточках из базы."""

from types import SimpleNamespace

from app.services.card_merge import find_pairs


def card(name, latin, origin, shots=0, uses=0):
    return SimpleNamespace(id=name, name=name, name_latin=latin, origin=origin,
                           photo_url=None, photo_attribution=None, shots=shots, uses=uses)


def test_pair_found_through_the_author_tail():
    """«Ficus elastica Roxb.» и «Ficus elastica» — одно растение на двух карточках."""
    rows = [card("Резиновое дерево", "Ficus elastica Roxb.", "herbarium", shots=32),
            card("Фикус каучуконосный", "Ficus elastica", "houseplant")]
    pairs = find_pairs(rows)
    assert len(pairs) == 1
    keep, drop = pairs[0]
    assert keep.origin == "herbarium" and drop.origin == "houseplant"


def test_capitals_do_not_hide_the_pair():
    rows = [card("Перец стрелковый однолетний", "CAPSICUM ANNUUM L.", "herbarium", shots=7),
            card("Стручковый перец однолетний", "Capsicum annuum", "houseplant")]
    assert len(find_pairs(rows)) == 1


def test_several_herbarium_cards_are_left_alone():
    """Где в гербарии уже свой разнобой, сначала разбираются внутри него."""
    rows = [card("Драцена", "Dracaena", "herbarium"),
            card("Драцена нераздельная", "Dracaena L.", "herbarium"),
            card("Драцена", "Dracaena", "houseplant")]
    assert find_pairs(rows) == []


def test_two_unrelated_species_are_not_a_pair():
    rows = [card("Фикус каучуконосный", "Ficus elastica", "houseplant"),
            card("Фикус карликовый", "Ficus pumila", "herbarium")]
    assert find_pairs(rows) == []
