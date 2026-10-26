"""Грибы: двойники из справочника пар и уровни по видам."""

from app.services.fungi_safety import fix_monograph, fix_text, identity_conflict, level_rule, lookalikes, safety_block


def test_twin_direction():
    # съедобный гриб получает ядовитого двойника
    assert lookalikes("Сморчок высокий", "Morchella elata") == ("строчок обыкновенный", None)
    assert lookalikes("Шампиньон полевой", "Agaricus arvensis")[0] == "бледная поганка"
    # ядовитый гриб получает съедобные грибы, за которые его принимают
    assert lookalikes("Строчок гигантский", "Gyromitra gigas") == (None, "со сморчками")
    assert lookalikes("Шампиньон желтокожий", "Agaricus xanthodermus") == (None, "со съедобными шампиньонами")
    assert lookalikes("Поганка бледная", "Amanita phalloides")[0] is None


def test_no_pair_no_twin():
    # случай из жалобы: строчок осенний с бледной поганкой не путают
    assert lookalikes("Строчок осенний", "Gyromitra infula") == (None, None)
    assert lookalikes("Паутинник согнутоножковый", "Cortinarius flexipes") == (None, None)
    assert lookalikes("Подгруздок черный", "Russula adusta") == (None, None)


def test_russula_genus_needs_name():
    assert lookalikes("Сыроежка", "Russula")[0] == "бледная поганка"
    assert lookalikes("Рыжики", "Russula")[0] is None


def test_levels_in_mixed_genera():
    assert level_rule("Мухомор цезарский, или кесарев гриб", "Amanita caesarea")["level"] == 1
    assert level_rule("Мухомор краснеющий", "Amanita rubescens")["level"] == 2
    assert level_rule("Поплавок свинцово-серый", "Amanita vaginata")["level"] == 2
    assert level_rule("Колпак кольчатый", "Cortinarius caperatus")["level"] == 1
    assert level_rule("Розитес", "Cortinarius caperatus (Pers.) Fr.")["level"] == 1
    assert level_rule("Поганка бледная", "Amanita phalloides")["level"] == 4
    assert level_rule("Мухомор красный", "Amanita muscaria")["level"] == 3
    assert level_rule("Мухомор красный", "Amanita muscaria")["label"] == "ядовито"
    # вид смешанного рода не из списка: не «смертельно», а «нет данных, есть нельзя»
    r = level_rule("Мухомор Виттадини", "Amanita vittadinii")
    assert r["level"] == 0 and "есть его нельзя" in r["rationale"]
    # род целиком без правила: решает модель с якорем
    assert level_rule("Строчок осенний", "Gyromitra infula") is None
    assert level_rule("Мухоморы", "Amanita") is None


def test_identity_conflict_blocks_rules():
    assert identity_conflict("Головач удлиненный", "Aconitum")
    assert identity_conflict("Строчки", "Morchella")
    assert lookalikes("Строчки", "Morchella") == (None, None)
    assert identity_conflict("Мухомор цезарский", "Amanita caesarea") is None


def test_text_drops_old_twin_and_contradictions():
    v = "Смертельно ядовитый гриб — есть нельзя. Опасный двойник — бледная поганка, не перепутай."
    assert fix_text(v, 4, level_changed=False) == "Смертельно ядовитый гриб — есть нельзя."
    assert fix_text(v, 2, level_changed=True) == ""
    keep = "Растёт в широколиственных лесах."
    assert fix_text(keep, 2, level_changed=True) == keep


def test_monograph_gets_rule_verdict_when_empty():
    mono = {"verdict": "Смертельно ядовитый гриб — есть нельзя. Опасный двойник — бледная поганка, не перепутай.",
            "safety": {"level": 4}, "roles": ["toxic"], "is_toxic": True}
    rule = level_rule("Поплавок серый", "Amanita vaginata")
    s = safety_block(rule["level"], rule["rationale"], rule["edible_parts"], [], "бледная поганка", None)
    out = fix_monograph(mono, s, curated=True)
    assert out["safety"]["level"] == 2 and out["safety"]["deadly_twin"] == "бледная поганка"
    assert out["is_toxic"] is False and "toxic" not in out["roles"]
    assert out["verdict"].startswith("Поплавки условно съедобны")
    assert "после варки" in out["verdict"]
    assert fix_monograph(out, s, curated=True) == out                     # повторный прогон ничего не меняет


def test_twin_clause_cut_keeps_head():
    v = "Условно съедобный гриб, требует тщательной обработки перед употреблением; опасный двойник — Тигровый мухомор, не перепутай."
    assert fix_text(v, 2, level_changed=False) == "Условно съедобный гриб, требует тщательной обработки перед употреблением."
    v = "СМЕРТЕЛЬНО ядовит — есть нельзя; опасный двойник — сыроежки, не перепутай."
    assert fix_text(v, 1, level_changed=True) == ""


def test_contradictions_after_level_change():
    assert fix_text("Съедобность исключена.", 2, level_changed=True) == ""
    assert fix_text("Мухомор поганковидный смертельно ядовит — есть его нельзя.", 3, level_changed=True) == ""


def test_poplavok_by_name():
    assert level_rule("Поплавок серый", "Amanita nivalis")["level"] == 2
    assert "королевский" not in level_rule("Мухомор поганковидный", "Amanita regalis")["rationale"]


def test_name_must_fit_genus():
    assert identity_conflict("Майский гриб", "Agaricus")
    assert identity_conflict("Пестрец колокольчатый", "Galerina")
    assert identity_conflict("омфалина каменистая", "Pleurotus tuber-regium")
    assert level_rule("омфалина каменистая", "Pleurotus tuber-regium") is None
    assert identity_conflict("Agaricus", "Agaricus") is None         # имя латиницей не проверяем
    assert identity_conflict("Лжноопёнок серно-жёлтый", "Hypholoma fasciculare") is None
