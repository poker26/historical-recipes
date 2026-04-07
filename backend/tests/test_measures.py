from app.utils.measures import convert_measure, reaumur_to_celsius


def test_convert_zolotnik():
    result = convert_measure(2, "золотник")
    assert result is not None
    value, unit = result
    assert abs(value - 8.54) < 0.01
    assert unit == "г"


def test_convert_shtof():
    result = convert_measure(1, "штоф")
    assert result is not None
    value, unit = result
    assert abs(value - 1.23) < 0.01
    assert unit == "л"


def test_convert_unknown():
    assert convert_measure(1, "неизвестная_мера") is None


def test_reaumur_to_celsius():
    assert reaumur_to_celsius(80) == 100.0
    assert reaumur_to_celsius(0) == 0.0
