"""Сверка цитат рассказа о растении. Примеры со страницы 481 «Жизни растений», т. 6."""

from app.services.houseplant_story import STORY_KINDS, canon, story_quote_grounded

PAGE = """На родине, в Южной Бразилии, филоденд-
рон имбе используют главным образом в на-
родной медицине как антисептик. Его воздуш-
ные корни используют как веревки, из коры
плетут корзины. Растёт и на Шри-Ланке."""


def test_quote_without_hyphenation_is_grounded():
    quote = "филодендрон имбе используют главным образом в народной медицине как антисептик"
    assert story_quote_grounded(quote, PAGE)


def test_quote_with_hyphen_space_is_grounded():
    quote = "Его воздуш- ные корни используют как веревки"
    assert story_quote_grounded(quote, PAGE)


def test_yo_and_hyphenated_name():
    assert story_quote_grounded("Растет и на Шри-Ланке", PAGE)


def test_invented_quote_rejected():
    assert not story_quote_grounded("филодендрон имбе едят в сыром виде", PAGE)


def test_short_quote_rejected():
    assert not story_quote_grounded("антисептик", PAGE)


def test_canon_joins_line_break_hyphen():
    assert canon("филоденд-\nрон") == "филодендрон"


def test_kinds_cover_card_sections():
    assert set(STORY_KINDS) == {"homeland", "history", "name", "biology",
                                "uses", "lore", "danger", "lookalikes"}
