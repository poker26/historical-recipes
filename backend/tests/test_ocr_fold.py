"""Замена букв-двойников в тексте Indra. Примеры взяты из пробы 19.10.2026."""

from app.services.ocr import fold_mixed_scripts


def test_cyrillic_inside_latin_name():
    assert fold_mixed_scripts("(A. сrassinervium)") == "(A. crassinervium)"
    assert fold_mixed_scripts("Monstera аcuminata") == "Monstera acuminata"


def test_latin_inside_russian_word():
    assert fold_mixed_scripts("На Mадагаскаре") == "На Мадагаскаре"
    assert fold_mixed_scripts("диффенбахия сегуuна") == "диффенбахия сегуина"


def test_clean_words_untouched():
    text = "сансевьера цейлонская (S. zeylanica), рис. 263"
    assert fold_mixed_scripts(text) == text


def test_tie_left_as_is():
    # Поровну букв из двух алфавитов: большинства нет, слово не трогаем.
    assert fold_mixed_scripts("aб") == "aб"
