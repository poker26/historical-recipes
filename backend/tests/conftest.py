"""Shared test fixtures."""

import pytest


@pytest.fixture
def sample_ocr_text():
    """Sample OCR text with typical artifacts."""
    return (
        "**42. Анисовая водка**\n\n"
        "Взять 2 золотника анису, 1 золотникъ тмину,\n"
        "1 штофъ хорошаго вина. Настаивать 2 недѣли\n"
        "въ тепломъ мѣстѣ. Процѣдить и подслащивать\n"
        "по вкусу.\n\n"
        "**43. Мятная водка**\n\n"
        "Взять 3 горсти свѣжей мяты, залить\n"
        "2 штофами водки. Настаивать 10 дней.\n"
    )


@pytest.fixture
def sample_broken_text():
    """Sample text with broken words (OCR artifact)."""
    return "п р и г о т о в л е н i е водки"


@pytest.fixture
def sample_pre_reform_text():
    """Sample pre-reform Russian text."""
    return "Взять 2 золотника зверобоя и 1 фунтъ сахару. Настаивать 2 недѣли въ тепломъ мѣстѣ при 60 градусахъ Реомюра."
