"""Tests for text normalization."""

from app.services.normalizer import normalize_orthography, _normalize_measures_inline


class TestNormalizeOrthography:
    def test_yat_replacement(self):
        assert normalize_orthography("недѣли") == "недели"
        assert normalize_orthography("мѣсто") == "место"

    def test_i_decimal_replacement(self):
        assert normalize_orthography("вiно") == "вино"
        assert normalize_orthography("Іюнь") == "Июнь"

    def test_fita_replacement(self):
        assert normalize_orthography("ѳимьянъ") == "фимьян"  # ѳ→ф, trailing ъ removed

    def test_trailing_hard_sign_removal(self):
        result = normalize_orthography("фунтъ сахару")
        assert result == "фунт сахару"

    def test_preserves_mid_word_hard_sign(self):
        result = normalize_orthography("объём")
        assert result == "объём"

    def test_full_sentence(self, sample_pre_reform_text):
        result = normalize_orthography(sample_pre_reform_text)
        assert "ѣ" not in result
        assert "зверобоя" in result
        assert "фунт" in result
        assert "недели" in result
        assert "месте" in result

    def test_empty_string(self):
        assert normalize_orthography("") == ""

    def test_modern_text_unchanged(self):
        modern = "Взять 2 золотника зверобоя."
        assert normalize_orthography(modern) == modern


class TestNormalizeMeasuresInline:
    def test_zolotnik(self):
        result = _normalize_measures_inline("2 золотника корицы")
        assert "(8.5 г)" in result
        assert "2 золотника" in result  # original preserved

    def test_shtof(self):
        result = _normalize_measures_inline("1 штоф водки")
        assert "(1.2 л)" in result

    def test_funt(self):
        result = _normalize_measures_inline("3 фунта сахару")
        assert "г)" in result or "кг)" in result

    def test_no_measures(self):
        text = "обычный текст без мер"
        assert _normalize_measures_inline(text) == text

    def test_decimal_values(self):
        result = _normalize_measures_inline("0,5 штофа воды")
        assert "л)" in result
