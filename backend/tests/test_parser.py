"""Tests for recipe parser."""

from app.services.parser import parse_recipes, detect_category, extract_ingredients


class TestDetectCategory:
    def test_vodka(self):
        assert detect_category("Анисовая водка. Взять 2 золотника анису.") == "водка"

    def test_liqueur(self):
        assert detect_category("Приготовление ликера из малины.") == "ликёр"

    def test_tincture(self):
        assert detect_category("Настойка зверобоя на спирту.") == "настойка"

    def test_balsam(self):
        assert detect_category("Бальзам целительный из трав.") == "бальзам"

    def test_ratafia(self):
        assert detect_category("Ратафия вишнёвая.") == "ратафия"

    def test_rozolia(self):
        assert detect_category("Розолия гвоздичная.") == "розолия"

    def test_unknown(self):
        assert detect_category("Просто текст.") == "другое"


class TestExtractIngredients:
    def test_extracts_zolotnik(self):
        text = "Взять 2 золотника корицы и 1 золотник гвоздики."
        ingredients = extract_ingredients(text)
        assert len(ingredients) >= 1
        assert any(i.unit.startswith("золотник") for i in ingredients)

    def test_extracts_funt(self):
        text = "3 фунта сахару растворить в воде."
        ingredients = extract_ingredients(text)
        assert len(ingredients) >= 1
        assert ingredients[0].amount == "3"

    def test_extracts_shtof(self):
        text = "Залить 2 штофа водки."
        ingredients = extract_ingredients(text)
        assert len(ingredients) >= 1
        assert any("штоф" in i.unit for i in ingredients)

    def test_no_ingredients(self):
        text = "Просто описание без конкретных мер."
        ingredients = extract_ingredients(text)
        assert len(ingredients) == 0


class TestParseRecipes:
    def test_parses_bold_numbered(self, sample_ocr_text):
        recipes = parse_recipes(sample_ocr_text)
        assert len(recipes) == 2
        assert recipes[0].name == "Анисовая водка"
        assert recipes[0].category == "водка"
        assert recipes[0].number == 42
        assert recipes[1].name == "Мятная водка"
        assert recipes[1].number == 43

    def test_extracts_ingredients(self, sample_ocr_text):
        recipes = parse_recipes(sample_ocr_text)
        # First recipe should have ingredients
        assert len(recipes[0].ingredients) >= 1

    def test_section_sign_format(self):
        text = "§ 441. Розовая вода\nВзять 2 фунта лепестков розы.\n§ 442. Лавандовая вода\nВзять 1 фунт лаванды."
        recipes = parse_recipes(text)
        assert len(recipes) == 2
        assert recipes[0].name == "Розовая вода"

    def test_empty_text(self):
        assert parse_recipes("") == []

    def test_no_recipes(self):
        result = parse_recipes("Обычный текст, не содержащий рецептов.")
        assert len(result) == 0
