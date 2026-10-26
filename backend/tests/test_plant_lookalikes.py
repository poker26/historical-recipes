"""Растения: двойники из справочника пар, имя и латынь должны совпасть."""

from app.services.plant_lookalikes import fix_monograph, lookalikes


def test_pairs():
    assert lookalikes("Купырь лесной (морковник лесной)", "Anthriscus sylvestris", 2) == ("болиголов пятнистый", None)
    assert lookalikes("Лук медвежий", "Allium ursinum", 1) == ("ландыш майский", None)
    assert lookalikes("Болиголов пятнистый, омег", "Conium maculatum", 4) == (None, "с купырём, дикой морковью и петрушкой")
    assert lookalikes("Ландыш майский", "Convallaria majalis", 3) == (None, "с черемшой")


def test_name_must_agree():
    # кокорыш на латыни петрушки двойника «кокорыш» не получает
    assert lookalikes("Собачья петрушка", "Petroselinum crispum subsp. crispum", 4) == (None, None)
    assert lookalikes("Петрушка кудрявая", "Petroselinum crispum", 1)[0] == "кокорыш (собачья петрушка)"
    assert lookalikes("Зонтик пестрый", "Aegopodium", 0) == (None, None)
    assert lookalikes("Царский корень", "Veratrum lobelianum", 1) == (None, None)


def test_no_pair_no_twin_and_poisonous_has_none():
    assert lookalikes("Борщевик пушистый", "Heracleum dissectum", 2) == (None, None)
    assert lookalikes("Кокорыш обыкновенный", "Aethusa cynapium", 4)[0] is None


def test_monograph_verdict_loses_twin():
    mono = {"verdict": "Съедобен. Опасный двойник — болиголов, не перепутай.", "safety": {"level": 1, "deadly_twin": "болиголов"}}
    out = fix_monograph(mono, None, None)
    assert out["verdict"] == "Съедобен." and out["safety"]["deadly_twin"] is None
    assert fix_monograph(out, None, None) == out


def test_twin_in_middle_of_sentence():
    from app.services.fungi_safety import fix_text
    v = "Сныть обыкновенная — съедобное растение, но требует осторожности: корни ядовиты, а при сборе важно не перепутать с опасным двойником — болиголовом."
    assert fix_text(v, 1, level_changed=False) == "Сныть обыкновенная — съедобное растение, но требует осторожности: корни ядовиты."
    v = "Томат безопасен и съедобен в зрелых плодах, но зелёные плоды и надземная часть растения ядовиты — не перепутайте."
    assert fix_text(v, 2, level_changed=False) == "Томат безопасен и съедобен в зрелых плодах, но зелёные плоды и надземная часть растения ядовиты."
