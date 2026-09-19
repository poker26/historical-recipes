"""Проверки нормализаторов ухода на живых фразах из книг.

Все строки ниже взяты из скана, а не придуманы: Хессайон «Всё о комнатных
растениях» (замиокулькас, юкка, акалифа), Воронцов «Уход за комнатными
растениями» (плюмбаго), Сааков 1985 (хлорофитум), Головкин 1989 (мюленбекия).
"""

from app.services.houseplant_care import (
    is_greenhouse_advice,
    normalize_light,
    normalize_temperature,
    normalize_text,
    normalize_watering,
    quote_is_grounded,
    reference_in,
)


def test_watering_two_seasons_in_one_phrase():
    rules = normalize_watering(
        "Поливайте обильно с весны до осени. Поливайте умеренно в зимний период."
    )
    by_season = {r["season"]: r["mode"] for r in rules}
    assert by_season["summer"] == "abundant"
    assert by_season["winter"] == "moderate"


def test_watering_dry_between_and_sparse_winter():
    rules = normalize_watering(
        "Позвольте поверхности почвы просохнуть между поливами, зимой поливайте скудно."
    )
    modes = {(r["season"], r["mode"]) for r in rules}
    assert (None, "dry_between") in modes
    assert ("winter", "sparse") in modes


def test_watering_keeps_days_only_when_the_book_says_them():
    rules = normalize_watering("Подкармливайте раз в 10—14 дней, поливайте обильно.")
    assert rules[0]["days_min"] == 10 and rules[0]["days_max"] == 14

    plain = normalize_watering("Поливайте обильно с весны до осени.")
    assert "days_min" not in plain[0]


def test_watering_twice_a_month():
    rules = normalize_watering("Два раза в месяц поливайте умеренно.")
    assert rules[0]["days_min"] == 15


def test_temperature_minimum_and_range():
    assert normalize_temperature("содержите в зимний период в прохладном месте (минимум 7°С)") == {
        "c_min": 7,
        "season": "winter",
    }
    assert normalize_temperature("Содержат зимой при температуре 10—14 °C.")["c_max"] == 14


def test_temperature_upper_warning():
    out = normalize_temperature("При температуре выше 23 °С растению необходима вентиляция.")
    assert out["c_max_warn"] == 23


def test_light_levels():
    assert normalize_light("Слегка тенистое место.")["level"] == "part_shade"
    assert normalize_light("Выберите самое светлое место, какое есть.")["level"] == "bright"
    assert normalize_light("необходим прямой солнечный свет")["level"] == "sun"


def test_greenhouse_advice_is_flagged():
    assert is_greenhouse_advice(
        "На осень и зиму их помещают в холодную или умеренно теплую оранжерею."
    )
    assert not is_greenhouse_advice("Пересаживайте весной каждые два года.")


def test_reference_to_the_front_matter():
    assert reference_in("Уход общий. Земельная смесь № 2.") == "уход общий"
    assert reference_in("Земельная смесь № 2. Размножают делением куста.") == "земельная смесь № 2"
    assert reference_in("Поливайте обильно летом.") is None


def test_hyphenation_from_the_scan_does_not_break_the_quote():
    scan = "Побеги сте¬\nлющиеся, тонкие, густо облиствленные."
    assert quote_is_grounded("Побеги стелющиеся, тонкие", scan)
    assert normalize_text(scan).startswith("Побеги стелющиеся")


def test_short_fragment_is_not_accepted_as_a_quote():
    assert not quote_is_grounded("полив", "Поливайте обильно с весны до осени.")


def test_ungrounded_fact_is_dropped():
    """Совет, которого нет в скане, не должен доехать до карточки."""
    from app.services.houseplant_care import build_care_facts

    page_text = "Поливайте обильно с весны до осени. Поливайте умеренно в зимний период."
    items = [
        {"field": "water", "quote": "Поливайте обильно с весны до осени", "text": "Поливайте обильно летом"},
        {"field": "feeding", "quote": "Подкармливайте раз в неделю круглый год",
         "text": "Подкармливайте раз в неделю"},
    ]
    facts = build_care_facts(items, page_text, page=54)
    assert [f.field_name for f in facts] == ["water"]


def test_two_seasons_become_two_facts():
    from app.services.houseplant_care import build_care_facts

    page_text = "Поливайте обильно с весны до осени. Поливайте умеренно в зимний период."
    items = [{"field": "water", "quote": page_text, "text": page_text}]
    facts = build_care_facts(items, page_text, page=54)
    assert {f.season for f in facts} == {"summer", "winter"}
    assert {f.value["mode"] for f in facts} == {"abundant", "moderate"}


def test_greenhouse_advice_is_marked_on_the_fact():
    from app.services.houseplant_care import build_care_facts

    page_text = ("На осень и зиму их помещают в холодную или умеренно теплую оранжерею.")
    items = [{"field": "placement", "quote": page_text, "text": page_text}]
    facts = build_care_facts(items, page_text, page=420)
    assert facts[0].greenhouse is True


def test_genus_header_forms_from_the_scan():
    """Заголовки Саакова после распознавания: разрядка, кириллица, автор.

    Все четыре строки взяты со страниц книги, а не придуманы.
    """
    from app.services.houseplant_care import parse_genus_header

    assert parse_genus_header("ACANTHUS L. — Акант")[0] == "Acanthus"
    assert parse_genus_header("MONSTERA SCHOTT — Монстера")[0] == "Monstera"
    assert parse_genus_header("JACOBI NIA Nees ex Moric. — Якобиния")[0] == "Jacobinia"
    assert parse_genus_header("SANSEVIERIA T h n n b . — Сансевиерия")[0] == "Sansevieria"


def test_genus_header_when_the_russian_name_is_unrelated():
    """У хойи Сааков пишет «Плющ восковидный»: подсказке верить нельзя."""
    from app.services.houseplant_care import parse_genus_header

    assert parse_genus_header("HOY A R. Br. — Плющ восковидный")[0] == "Hoya"


def test_genus_header_when_the_russian_name_is_broken_by_ocr():
    """«Гипоэстес» распозналось как «Ги 10эстес» — имя берём из латыни."""
    from app.services.houseplant_care import parse_genus_header

    assert parse_genus_header("H Y P O E ST E S Soland. ex R . Br. — Ги 10эстес")[0] == "Hypoestes"


def test_articles_are_cut_at_headers_not_at_pages():
    from app.services.houseplant_care import find_genus_articles

    pages = [
        "Род ACANTHUS L. — Акант\nЛистья крупные.",
        "Состав земли: дерновая — 1 ч.\nРод BOWIEA Harv. — Бовея\nВьющиеся растения.",
    ]
    articles = find_genus_articles(pages)
    assert [a.genus_latin for a in articles] == ["Acanthus", "Bowiea"]
    # уход со второй страницы принадлежит первой статье, а не второй
    assert "Состав земли" in articles[0].text
    assert articles[1].page == 2


def test_watering_advice_is_not_left_in_the_dormancy_field():
    """Пилот на Саакове: «Зимой поливку ограничивают» модель кладёт в покой."""
    from app.services.houseplant_care import build_care_facts

    page_text = ("Зимой поливку ограничивают, однако земляной ком не доводят "
                 "до полной просушки.")
    items = [{"field": "dormancy", "quote": page_text, "text": page_text}]
    facts = build_care_facts(items, page_text, page=223)
    assert facts[0].field_name == "water"
    assert facts[0].season == "winter"
    assert facts[0].value["mode"] == "sparse"


def test_shading_is_recognised_as_light():
    from app.services.houseplant_care import normalize_light

    assert normalize_light("притенение от солнца")["level"] == "part_shade"
    assert normalize_light("Местоположение рекомендуется светлое")["level"] == "bright"


def test_propagation_paragraph_with_degrees_stays_propagation():
    """Пилот на хойе: абзац про черенки уехал в температуру из-за «20°»."""
    from app.services.houseplant_care import build_care_facts

    page_text = ("Растения размножают черенками весной и осенью. Черенки режут с одной, "
                 "двумя парами листьев. Оптимальная температура для укоренения не менее 20°.")
    items = [{"field": "propagation", "quote": page_text, "text": page_text}]
    facts = build_care_facts(items, page_text, page=223)
    assert facts[0].field_name == "propagation"


def test_latin_from_nowhere_is_rejected():
    """Пилот на Воронцове: свинчатке приписали латынь замиокулькаса."""
    from app.services.houseplant_care import verify_latin

    page = ("Для обильного цветения свинчатке необходим прямой солнечный свет. "
            "Лучшим субстратом для плюмбаго считается смесь из дерновой земли.")
    assert verify_latin("Zamioculcas zamiifolia", "свинчатка", page) == ""


def test_latin_survives_when_the_text_names_it():
    from app.services.houseplant_care import verify_latin

    page = "Плюмбаго ушковидное (Plumbago auriculata) родом из Южной Африки."
    assert verify_latin("Plumbago auriculata", "плюмбаго", page) == "Plumbago auriculata"


def test_latin_survives_when_the_russian_name_confirms_it():
    """У Хессайона латынь распознана в труху, но «Замиокулькас» её узнаёт."""
    from app.services.houseplant_care import verify_latin

    page = "2атюси1са5 гатНоНа Замиокулькас замиелистный единственный вид."
    assert verify_latin("Zamioculcas zamiifolia", "Замиокулькас", page) == "Zamioculcas zamiifolia"


def test_long_paragraph_is_cut_into_fields():
    """Абзац Воронцова про размещение: там же окна, подпорка и зимние градусы."""
    from app.services.houseplant_care import build_care_facts

    page_text = (
        "Для обильного цветения плюмбаго необходим прямой солнечный свет, но она должна "
        "быть защищена от жарких лучей полуденного солнца, поэтому лучше всего подойдут "
        "окна, выходящие на юг или восток. Растение следует поместить поближе к стене и "
        "подготовить для стеблей небольшую подпорку. Летом плюмбаго выносите в сад или на "
        "открытый балкон, зимой растение лучше содержать в прохладном помещении при "
        "температуре 8 — 12 °С."
    )
    facts = build_care_facts(
        [{"field": "placement", "quote": page_text, "text": page_text}], page_text, page=121
    )
    by_field = {f.field_name: f for f in facts}
    assert "light" in by_field and "temperature" in by_field
    assert by_field["temperature"].value["c_min"] == 8
    assert by_field["temperature"].value["c_max"] == 12
    # у каждой части своя цитата, а не общий абзац
    assert "температуре" in by_field["temperature"].quote
    assert "температуре" not in by_field["light"].quote


def test_short_advice_is_not_cut():
    from app.services.houseplant_care import split_into_field_parts

    parts = split_into_field_parts("Пересаживайте весной каждые два года.", "repotting")
    assert parts == [("repotting", "Пересаживайте весной каждые два года.")]


def test_latin_written_in_capitals_is_normalised():
    from app.services.houseplant_care import normalize_latin

    assert normalize_latin("TRICHOCAULON") == "Trichocaulon"
    assert normalize_latin("HOYA CARNOSA") == "Hoya carnosa"
    assert normalize_latin("Plumbago auriculata") == "Plumbago auriculata"


def test_division_is_propagation_not_repotting():
    """«Деление растений во время пересадки» — это размножение."""
    from app.services.houseplant_care import classify_sentence

    assert classify_sentence("Деление растений во время пересадки.", "repotting") == "propagation"


def test_gbif_accepted_name_is_taken():
    from app.services.houseplant_care import read_gbif_match

    data = {"canonicalName": "Hoya", "confidence": 94, "kingdom": "Plantae",
            "rank": "GENUS", "status": "ACCEPTED"}
    assert read_gbif_match(data, "Hoya") == ("Hoya", True)


def test_gbif_synonym_is_replaced_by_the_accepted_name():
    """«Zebrina pendula» это традесканция зебровидная, иначе книги разойдутся."""
    from app.services.houseplant_care import read_gbif_match

    data = {"canonicalName": "Zebrina pendula", "confidence": 97, "kingdom": "Plantae",
            "rank": "SPECIES", "status": "SYNONYM", "species": "Tradescantia zebrina",
            "genus": "Tradescantia"}
    assert read_gbif_match(data, "Zebrina pendula") == ("Tradescantia zebrina", True)


def test_unknown_name_stays_but_is_marked_unverified():
    """GBIF не знает «Stepttanotis» ни точно, ни нечётко — выдумывать нельзя."""
    from app.services.houseplant_care import read_gbif_match

    data = {"canonicalName": None, "confidence": 100, "matchType": "NONE"}
    assert read_gbif_match(data, "Stepttanotis") == ("Stepttanotis", False)


def test_rank_mismatch_is_not_accepted():
    """Спросили род, а ответили видом: такое совпадение не принимаем."""
    from app.services.houseplant_care import read_gbif_match

    data = {"canonicalName": "Hoya carnosa", "confidence": 99, "kingdom": "Plantae",
            "rank": "SPECIES", "status": "ACCEPTED"}
    assert read_gbif_match(data, "Hoya") == ("Hoya", False)


def test_cultivar_and_subspecies_are_stripped_before_lookup():
    from app.services.houseplant_care import latin_for_lookup

    assert latin_for_lookup("Ctenanthe oppenheimiana 'variegata'") == "Ctenanthe oppenheimiana"
    assert latin_for_lookup("Ceropegia linearis ssp. woodii") == "Ceropegia linearis"
    assert latin_for_lookup("Hoya") == "Hoya"


def test_higher_rank_match_is_refused():
    """У Duvalia справочник отвечает «Plantae» с уверенностью 96."""
    from app.services.houseplant_care import read_gbif_match

    data = {"canonicalName": "Plantae", "confidence": 96, "kingdom": "Plantae",
            "rank": "KINGDOM", "matchType": "HIGHERRANK"}
    assert read_gbif_match(data, "Duvalia") == ("Duvalia", False)


def test_glossary_keys_are_canonical():
    """«Земельная смесь №2» и «земельная смесь № 2» — один ключ."""
    from app.services.houseplant_care import canonical_reference_key as key

    assert key("Земельная смесь №2") == "земельная смесь № 2"
    assert key("земельная смесь № 2") == "земельная смесь № 2"
    assert key("Уход общий") == "уход общий"
    assert key("Общий уход за растениями") == "уход общий"
    assert key("Пересадка") is None


def test_glossary_key_matches_what_articles_reference():
    """Ключ словаря обязан совпасть со ссылкой, найденной в статье о виде."""
    from app.services.houseplant_care import canonical_reference_key, reference_in

    article = "Содержат зимой при температуре 10—14 °C. Уход общий. Земельная смесь № 2."
    assert canonical_reference_key("Земельная смесь № 2") == "земельная смесь № 2"
    assert reference_in(article) == "уход общий"


def test_degrees_without_the_letter_are_read():
    """Сааков пишет «20—22°» и «до 16°» — без буквы C."""
    from app.services.houseplant_care import normalize_temperature

    assert normalize_temperature("при температуре 20—22° и высокой влажности")["c_max"] == 22
    assert normalize_temperature("После отцветания температуру снижают до 16°.")["c_max_warn"] == 16


def test_light_phrases_from_saakov():
    from app.services.houseplant_care import normalize_light

    assert normalize_light("Агавы — светолюбивые растения.")["level"] == "sun"
    assert normalize_light("Выращивается при хорошем освещении")["level"] == "bright"
    assert normalize_light("Содержат в осветленных местах.")["level"] == "bright"


def test_section_heading_is_not_an_advice():
    """«Размножение:» — заголовок рубрики, а не совет."""
    from app.services.houseplant_care import build_care_facts

    page_text = "Размножение: черенками весной."
    items = [{"field": "propagation", "quote": page_text, "text": "Размножение:"}]
    assert build_care_facts(items, page_text, page=1) == []


def test_author_surname_is_stripped_before_lookup():
    """Книга печатает автора сразу за именем: «Dianella Lam.», «Callicarpa l.»."""
    from app.services.houseplant_care import latin_for_lookup

    assert latin_for_lookup("Dianella Lam.") == "Dianella"
    assert latin_for_lookup("Callicarpa l.") == "Callicarpa"
    assert latin_for_lookup("Sanchezia Ruiz") == "Sanchezia"
    assert latin_for_lookup("Goldfussia Nees") == "Goldfussia"
    # настоящий биномен не трогаем: эпитет вида со строчной и без точки
    assert latin_for_lookup("Hoya carnosa") == "Hoya carnosa"
    assert latin_for_lookup("Zamioculcas zamiifolia") == "Zamioculcas zamiifolia"


def test_backbone_search_takes_only_the_exact_plant_match():
    """Поиск возвращает и животных, и похожие имена: берём только своё."""
    import asyncio
    from unittest.mock import patch
    import app.services.houseplant_care as hc

    payload = {"results": [
        {"canonicalName": "Duvalia", "kingdom": "Animalia"},
        {"canonicalName": "Duvalius", "kingdom": "Animalia"},
        {"canonicalName": "Fittonia", "kingdom": "Plantae"},
    ]}

    class FakeResponse:
        def json(self):
            return payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    with patch("httpx.AsyncClient", lambda *a, **k: FakeClient()):
        assert asyncio.run(hc._search_backbone("Fittonia", "GENUS")) == "Fittonia"
        assert asyncio.run(hc._search_backbone("Duvalia", "GENUS")) == ""


def test_soil_mix_reads_as_a_recipe():
    """Строка таблицы Головкина должна читаться как состав, а не как таблица."""
    from app.services.houseplant_care import describe_soil_mix

    mix = {"number": 8, "parts": {"листовая земля": "1", "хвойная земля": "1",
                                  "перегной": "0,5", "торф": "1"},
           "note": "+ 2 части сфагнума"}
    assert describe_soil_mix(mix) == ("листовая земля 1, хвойная земля 1, перегной 0,5, "
                                      "торф 1, 2 части сфагнума (части по объёму)")
    assert describe_soil_mix({"number": 9, "parts": {}}) == ""


def test_russian_name_for_the_card_header():
    """«Ф. упругий» и «АЛОКАЗИЯ» в заголовке карточки читателю не годятся."""
    from app.services.houseplant_care import pick_russian_name

    assert pick_russian_name(["Ф. упругий", "Фикус", "Фикус"]) == "Фикус"
    assert pick_russian_name(["АЛОКАЗИЯ"]) == "Алоказия"
    assert pick_russian_name(["С. спаржевидная", "С. густоцветковая"]) == "С. спаржевидная"
    assert pick_russian_name([]) is None


def test_cyrillic_lookalikes_inside_a_latin_name_are_fixed():
    """Распознавание даёт «Aglaoнema»: на глаз верно, а справочник не находит."""
    from app.services.houseplant_care import normalize_latin

    assert normalize_latin("Aglaoнema") == "Aglaonema"
    assert normalize_latin("Ficus") == "Ficus"          # чистую латынь не трогаем
    assert normalize_latin("Ахименес") == "Ахименес"    # чисто русское имя тоже


def test_card_monograph_keeps_book_voices_and_order():
    """Карточка комнатного: разделы по порядку, фразы книг как есть."""
    from types import SimpleNamespace
    from app.services.houseplant_cards import build_monograph

    rows = [
        SimpleNamespace(field="water", season="summer", value_text="Поливайте обильно.",
                        taxon_ru="Замиокулькас", book="Всё о комнатных растениях"),
        SimpleNamespace(field="water", season="winter", value_text="зимой поливайте скудно.",
                        taxon_ru="Замиокулькас", book="Всё о комнатных растениях"),
        SimpleNamespace(field="light", season=None, value_text="Слегка тенистое место.",
                        taxon_ru="Замиокулькас", book="Всё о комнатных растениях"),
    ]
    mono = build_monograph("Zamioculcas zamiifolia", rows, None)

    assert mono["name"] == "Замиокулькас"
    assert mono["origin"] == "houseplant"
    # свет идёт раньше полива: с него начинается карточка
    assert mono["care_sections"][0].startswith("Свет")
    assert "Поливайте обильно" in mono["care_sections"][1]
    assert mono["sources"] == ["Всё о комнатных растениях"]
    assert mono["lead_fact"]["text"].startswith("Летом")


def test_card_for_a_genus_says_it_covers_varieties():
    from types import SimpleNamespace
    from app.services.houseplant_cards import build_monograph

    rows = [SimpleNamespace(field="light", season=None, value_text="Светлое место.",
                            taxon_ru="Хойя", book="Сааков")]
    mono = build_monograph("Hoya", rows, None)
    assert "сортов" in mono["verdict"]


def test_toxicity_article_header_from_the_book():
    """Заголовки пособия: «1.1. Агава американская — Agave americana L.»"""
    from app.services.houseplant_toxicity import find_articles

    pages = [
        "ГЛАВА 1.\n1.1. Агава американская — Agave americana L.\n"
        "Семейство Агавовые — Agavaceae\n"
        + "Ботаническое описание. Род агава насчитывает 300 видов. " * 12,
        "Первая помощь: промыть.\n1.2. Диффенбахия пятнистая — Dieffenbachia maculata\n"
        + "Сок вызывает ожог слизистой рта и глотки. " * 20,
    ]
    articles = find_articles(pages)
    assert [a[0] for a in articles] == ["Agave americana", "Dieffenbachia maculata"]
    # первая помощь со второй страницы принадлежит первой статье
    assert "Первая помощь" in articles[0][2]
    assert articles[1][3] == 2


def test_severity_is_graded_by_the_books_own_words():
    from app.services.houseplant_toxicity import grade_severity

    assert grade_severity("Сок вызывает ожог слизистой и жжение") == "irritant"
    assert grade_severity("Вызывает рвоту и судороги") == "toxic"
    assert grade_severity("Возможен смертельный исход при остановке сердца") == "dangerous"
    assert grade_severity("Растение неприхотливо") == ""


def test_toxicity_header_survives_cyrillic_latin():
    """Распознаватель пишет латынь кириллицей: «Адауе атепсапа (Е.»"""
    from app.services.houseplant_toxicity import find_articles

    pages = ["ГЛАВА 1.\n1.1. Агава американская — Адауе атепсапа (Е.\n"
             "Семейство Агавовые — Адауасеае\n"
             + "Сок вызывает дерматит при попадании на кожу. " * 20,
             "1.2. Диффенбахия пятнистая — Dieffenbachia maculata\n"
             + "Ожог слизистой рта и глотки. " * 30]
    articles = find_articles(pages)
    assert [a[1] for a in articles] == ["Агава американская", "Диффенбахия пятнистая"]
    # у первой латынь не читается, у второй читается
    assert articles[0][0] == ""
    assert articles[1][0] == "Dieffenbachia maculata"


def test_card_warning_puts_danger_first():
    """Предупреждение человек должен прочитать раньше советов про полив."""
    from types import SimpleNamespace
    from app.services.houseplant_cards import build_monograph

    care = [SimpleNamespace(field="water", season=None, value_text="Поливайте умеренно.",
                            taxon_ru="Аглаонема", book="Всё о комнатных растениях")]
    danger = [SimpleNamespace(severity="toxic", parts=["все части растения", "особенно плоды"],
                              symptoms="Тошнота, рвота, слюнотечение.",
                              first_aid="Промыть желудок.",
                              children="Дети травятся привлекательными плодами.",
                              pets="", page=11, book="Комнатные ядовитые растения")]
    mono = build_monograph("Aglaonema commutatum", care, None, danger)

    assert mono["is_toxic"] is True
    assert mono["verdict"].startswith("Растение ядовито")
    assert mono["cautions"]["toxic_parts"] == ["все части растения", "особенно плоды"]
    assert mono["cautions"]["symptoms"].startswith("Тошнота")
    assert "Дети:" in mono["cautions"]["text"]
    assert "Первая помощь:" in mono["cautions"]["text"]
    assert mono["cautions"]["page"] == 11


def test_card_without_danger_stays_plain():
    from types import SimpleNamespace
    from app.services.houseplant_cards import build_monograph

    care = [SimpleNamespace(field="light", season=None, value_text="Светлое место.",
                            taxon_ru="Хлорофитум", book="Сааков")]
    mono = build_monograph("Chlorophytum", care, None, [])
    assert mono["is_toxic"] is False
    assert "cautions" not in mono


def test_contents_lines_are_not_mistaken_for_articles():
    """Оглавление повторяет все заголовки — статьи находились дважды."""
    from app.services.houseplant_toxicity import find_articles

    real = "1.19. Диффенбахия пятнистая — Dieffenbachia maculata\n" + ("Сок ядовит. " * 60)
    toc = "1.19. Диффенбахия пятнистая — Dieffenbachia maculata ....... 56\n"
    articles = find_articles([real, toc])
    assert len(articles) == 1
    assert articles[0][1] == "Диффенбахия пятнистая"


def test_first_aid_reference_becomes_a_usable_line():
    """Пособие отсылает к разделу агавы; в карточке такая отсылка бесполезна."""
    from app.services.houseplant_cards import short_first_aid

    book = ("помощь как при отравлении частями тех растений, в которых накапливаются "
            "сильнодействующие, токсичные и ядовитые вещества (см. соответствующий "
            "раздел при описании агавы американской)")
    assert short_first_aid(book) == "вызвать рвоту, принять активированный уголь и обратиться к врачу"


def test_first_aid_keeps_the_books_own_first_step():
    from app.services.houseplant_cards import short_first_aid

    book = ("необходимо скорейшее удаление содержимого желудочно-кишечного тракта. "
            "В случае отсутствия спонтанной рвоты необходимо выпить растворы поваренной соли")
    assert short_first_aid(book) == "необходимо скорейшее удаление содержимого желудочно-кишечного тракта."


def test_symptoms_are_clipped_to_a_readable_line():
    from app.services.houseplant_cards import clip

    book = ("Экстракардиальные нарушения: со стороны ЖКТ (анорексия, тошнота, рвота, "
            "икота, диарея, боли в животе), со стороны центральной нервной системы "
            "(головная боль, головокружение, спутанность сознания, делирий, судороги), "
            "со стороны органов зрения (нечёткость зрения, светобоязнь)")
    out = clip(book, 220)
    assert len(out) <= 220 and out.endswith("…")


def test_warning_lines_do_not_run_together_or_double_the_dot():
    """«плодов аглаонемы Первая помощь» и «тракта..» — обе беды из одного места."""
    from app.services.houseplant_cards import sentence

    assert sentence("поедании ими привлекательных плодов аглаонемы").endswith("аглаонемы.")
    assert sentence("удаление содержимого тракта.") == "удаление содержимого тракта."
