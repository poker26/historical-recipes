"""Ароидные: уровень по правилу и чистка первой фразы карточки."""

from app.services.aroid_safety import fix_monograph, fix_verdict
from app.services.edible_safety import AROID_VERDICT, aroid_rule


def test_rule_levels():
    assert aroid_rule("Zamioculcas zamiifolia")["level"] == 3
    assert aroid_rule("Spathiphyllum")["level"] == 3
    assert aroid_rule("Epipremnum aureum")["level"] == 3
    assert aroid_rule("Monstera")["level"] == 3
    assert aroid_rule("Monstera deliciosa")["level"] == 2
    assert aroid_rule("Colocasia esculenta")["level"] == 2
    assert aroid_rule("Xanthosoma sagittifolium")["level"] == 2
    assert aroid_rule("Ficus elastica") is None
    assert aroid_rule(None) is None


def test_verdict_drops_deadly_and_keeps_homeland():
    v = ("СМЕРТЕЛЬНО ядовито — ни в коем случае нельзя употреблять в пищу, опасно даже при контакте с соком. "
         "Африканский род, у которого сложные листья однаждыперистые.")
    out = fix_verdict(v, 3)
    assert out == "Африканский род, у которого сложные листья однаждыперистые."
    assert fix_verdict(out, 3) == out                       # повторный прогон ничего не меняет


def test_verdict_drops_no_data_and_safe_claims():
    assert fix_verdict("Недостаточно данных для оценки безопасности и применения растения.", 3) == ""
    assert fix_verdict("Колоказия съедобная — безопасное и ценное пищевое растение.", 2) == ""
    # Прежняя фраза правила (до 20.10.2026) повторяла плашку и тоже убирается.
    assert fix_verdict(AROID_VERDICT[3] + " Растёт в Бразилии.", 3) == "Растёт в Бразилии."


def test_monograph_safety_block_and_roles():
    rule = aroid_rule("Zamioculcas zamiifolia")
    mono = {"verdict": "СМЕРТЕЛЬНО ядовито.", "roles": ["toxic"], "is_toxic": True,
            "safety": {"level": 4, "label": "смертельно ядовито", "rationale": "нефрит, уремия"}}
    out = fix_monograph(mono, rule)
    assert out["safety"]["level"] == 3
    assert "нефрит" not in out["safety"]["rationale"]
    assert out["roles"] == ["toxic"] and out["is_toxic"] is True
    assert fix_monograph(out, rule) == out                  # идемпотентно


def test_monstera_fruit_verdict_kept():
    rule = aroid_rule("Monstera deliciosa")
    mono = {"verdict": "Плоды монстеры условно съедобны — только полностью спелые.", "roles": ["edible"]}
    out = fix_monograph(mono, rule)
    assert out["verdict"] == mono["verdict"]
    assert out["is_toxic"] is False and "toxic" not in out["roles"]


def test_boilerplate_removed_from_verdict():
    v = ("Растение ядовито при попадании внутрь. Комнатное растение. Здесь собрано то, что о нём пишут "
         "книги по комнатному цветоводству: свет, полив, земля, пересадка и размножение. Книги описывают "
         "весь род, поэтому совет годится и для сортов.")
    assert fix_verdict(v, 3) == ""
