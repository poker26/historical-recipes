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
