"""Проверки разбора ответа Викимедиа.

Коды лицензий и подписи взяты из живых ответов справочника по фикусу
каучуконосному и замиокулькасу.
"""

from app.services.wikimedia import license_is_free, plain


def test_creative_commons_is_allowed():
    assert license_is_free("cc-by-sa-3.0")
    assert license_is_free("cc-by-4.0")
    assert license_is_free("cc0")
    assert license_is_free("CC BY-SA 3.0")


def test_public_domain_is_allowed():
    assert license_is_free("pd")
    assert license_is_free("Public domain")


def test_fair_use_is_refused():
    """«Добросовестное использование» разрешает энциклопедию, но не наше приложение."""
    assert not license_is_free("fair use")
    assert not license_is_free("non-free")
    assert not license_is_free("")


def test_author_comes_without_markup():
    html = '<span class="int-own-work" lang="ru">Собственная работа</span>'
    assert plain(html) == "Собственная работа"
    assert plain('<a href="//commons.wikimedia.org/wiki/User:Ies">Frank Vincentz</a>') == "Frank Vincentz"


def test_redirect_chain_is_followed():
    """Спросили Abutilon, а статья называется «Канатник»."""
    from app.services.wikimedia import title_map

    payload = {"query": {"redirects": [{"from": "Abutilon", "to": "Канатник"}]}}
    assert title_map(payload)["Abutilon"] == "Канатник"


def test_spelling_fix_then_redirect():
    """Справочник сначала правит написание, потом ведёт по редиректу."""
    from app.services.wikimedia import title_map

    payload = {"query": {
        "normalized": [{"from": "ficus elastica", "to": "Ficus elastica"}],
        "redirects": [{"from": "Ficus elastica", "to": "Фикус каучуконосный"}],
    }}
    assert title_map(payload)["ficus elastica"] == "Фикус каучуконосный"


def test_attribution_names_the_author_and_the_licence():
    from app.services.wikimedia import attribution_from

    meta = {"Artist": {"value": '<a href="#">Frank Vincentz</a>'},
            "LicenseShortName": {"value": "CC BY-SA 3.0"}}
    assert attribution_from(meta) == "Frank Vincentz, CC BY-SA 3.0, Викимедиа"


def test_file_name_is_matched_across_both_spellings():
    """В статье файл «Hoya_carnosa.jpg», на его странице — «Файл:Hoya carnosa.jpg»."""
    from app.services.wikimedia import file_key

    assert file_key("Hoya_carnosa_20080928.jpg") == file_key("Hoya carnosa 20080928.jpg")
