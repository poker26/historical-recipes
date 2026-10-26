"""Замена букв-двойников в тексте Indra. Примеры взяты из пробы 19.10.2026."""

from app.services.ocr import body_text, column_order, fold_mixed_scripts, is_caption


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


def test_column_order_keeps_article_under_its_heading():
    # Координаты блоков Indra со стр. 394 Головкина (26.10.2026): уход ховеи
    # стоит в правой колонке выше заголовка «Хойя», а Indra отдавала его после.
    def block(label, x0, y0, x1, y1, text):
        return {"label": label, "bbox": [x0, y0, x1, y1], "text": text}
    blocks = [
        block("Section-Header", 71.9, 588.8, 1017.5, 624.3, "Ховея, кентия — Howea"),
        block("Text", 69.2, 715.6, 652.4, 851.1, "Именуется по названию о. Лорд-Хау"),
        block("Text", 46.6, 857.5, 652.3, 1213.2, "Х. Бельмора — H. belmoreana"),
        block("Text", 694.6, 714.5, 1282.9, 1069.7, "Х. Форстера — H. forsteriana"),
        block("Section-Header", 66.9, 1286.0, 525.0, 1325.9, "Хойя — Hoya R. Br."),
        block("Text", 68.2, 1589.8, 652.3, 2019.1, "Х. мясистая — H. carnosa"),
        block("Text", 702.9, 1075.8, 1282.9, 1209.8, "Зимой содержат при температуре 14—18 °С."),
        block("Text", 699.6, 1407.0, 1287.1, 1720.1, "Цветет весной и летом."),
        block("Page-Footer", 69.7, 2062.2, 123.3, 2083.9, "394"),
    ]
    order = [b["text"] for b in column_order(blocks)]
    assert order.index("Зимой содержат при температуре 14—18 °С.") < order.index("Хойя — Hoya R. Br.")
    assert order.index("Х. Бельмора — H. belmoreana") < order.index("Х. Форстера — H. forsteriana")
    assert order.index("Х. мясистая — H. carnosa") < order.index("Цветет весной и летом.")


def test_column_order_without_boxes_is_unchanged():
    blocks = [{"label": "Text", "text": "б"}, {"label": "Text", "text": "а"}]
    assert column_order(blocks) == blocks
