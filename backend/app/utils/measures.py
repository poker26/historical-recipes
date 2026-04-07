"""Historical measurement conversion utilities."""

# Russian historical measures -> modern equivalents
MEASURES = {
    # Weight
    "пуд": {"value": 16.38, "unit": "кг"},
    "фунт": {"value": 409.5, "unit": "г"},
    "лот": {"value": 12.8, "unit": "г"},
    "золотник": {"value": 4.27, "unit": "г"},
    "доля": {"value": 44.43, "unit": "мг"},

    # Volume
    "ведро": {"value": 12.3, "unit": "л"},
    "штоф": {"value": 1.23, "unit": "л"},
    "бутылка": {"value": 0.615, "unit": "л"},  # винная
    "чарка": {"value": 0.123, "unit": "л"},
    "шкалик": {"value": 0.0615, "unit": "л"},
    "гарнец": {"value": 3.28, "unit": "л"},
    "четверть": {"value": 3.075, "unit": "л"},

    # Temperature
    "градус реомюра": {"value": 1.25, "unit": "°C", "type": "multiplier"},
}


def convert_measure(value: float, unit_old: str) -> tuple[float, str] | None:
    """Convert historical measure to modern equivalent."""
    key = unit_old.lower().strip()
    if key not in MEASURES:
        return None
    m = MEASURES[key]
    if m.get("type") == "multiplier":
        return (value * m["value"], m["unit"])
    return (value * m["value"], m["unit"])


def reaumur_to_celsius(degrees: float) -> float:
    """Convert Réaumur to Celsius."""
    return degrees * 1.25
