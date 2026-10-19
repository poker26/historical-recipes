"""Замена букв-двойников в тексте Indra. Примеры взяты из пробы 19.10.2026."""

from app.services.ocr import body_text, fold_mixed_scripts, is_caption


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


def test_caption_blocks():
    # Блоки со страницы 481 тома 6 «Жизни растений».
    assert is_caption("Рис. 271. Аронииковые: подсемейство филодендровые")
    assert is_caption("13 — стерильный цветок; 14 — продольный разрез завязи.\n15 — общий вид;")
    assert is_caption("женской стадии цветения (покрывало удалено); 2 — початок")
    body = ("образован 4—5 плодолистиками, что отчетливо видно по еще несросшимся "
            "4—5 рыльцам, но завязь уже одногнездная, с 1—2 ортотропными семязачатками")
    assert not is_caption(body)


def test_body_text_drops_furniture():
    blocks = [
        {"label": "Page-Header", "text": "18"},
        {"label": "Table", "text": "19\n10"},
        {"label": "Text", "text": "Рис. 271. Аронииковые"},
        {"label": "Text", "text": "На родине, в Южной Бразилии, филодендрон имбе используют"},
        {"label": "Page-Footer", "text": "481"},
    ]
    assert body_text(blocks) == "На родине, в Южной Бразилии, филодендрон имбе используют"
