"""«Мой подоконник»: что книги советуют делать с растением в этот сезон.

Человек положил растение на подоконник, и главный его вопрос — что делать с ним
сейчас. Ответ берётся из карточки: в разделах ухода лежат фразы книг с источником,
страницей и сезоном, если книга его назвала. Здесь из них выбираются те, что
относятся к текущему сезону.

Частоту полива в днях книги почти не называют (интервал есть в 5 правилах из
1064), поэтому ни здесь, ни в пушах нет «полейте сегодня». Есть фраза книги про
этот сезон и просьба проверить землю.

Сезоны. Книги различают только лето и зиму. Зимой (ноябрь–февраль) и летом
(май–август) берём фразы своего сезона, а где их нет, фразы без сезона. Весной
(март–апрель) на первом месте пересадка и подкормка: их начинают именно тогда.
Осенью (сентябрь–октябрь) показываем зимние советы заранее: режим меняют до
холодов.
"""

from __future__ import annotations

from datetime import date

SEASON_RU = {"winter": "зимой", "spring": "весной", "summer": "летом", "autumn": "осенью"}

# (раздел ухода, сезон фраз книги, который подходит)
_PLAN = {
    "winter": [("water", "winter"), ("temperature", "winter"), ("light", None), ("dormancy", None)],
    "spring": [("repotting", None), ("feeding", None), ("water", None), ("light", None)],
    "summer": [("water", "summer"), ("light", None), ("humidity", None), ("feeding", None)],
    "autumn": [("water", "winter"), ("temperature", "winter"), ("light", None)],
}
_OPPOSITE = {"winter": "summer", "summer": "winter"}
MAX_LINES = 3


def season_of(day: date) -> str:
    m = day.month
    if m in (11, 12, 1, 2):
        return "winter"
    if m in (3, 4):
        return "spring"
    if m in (5, 6, 7, 8):
        return "summer"
    return "autumn"


def advice_now(monograph: dict | None, season: str) -> list[dict]:
    """До трёх фраз книг для этого сезона: раздел, фраза, книга, страница."""
    care = (monograph or {}).get("care") or []
    by_field = {b.get("field"): b for b in care if isinstance(b, dict)}
    lines: list[dict] = []
    for field, wanted in _PLAN[season]:
        block = by_field.get(field)
        if not block:
            continue
        voices = [v for v in block.get("voices") or [] if (v.get("text") or "").strip()]
        own = [v for v in voices if v.get("season") == wanted] if wanted else []
        plain = [v for v in voices if not v.get("season")]
        # фразу про лето зимой не показываем, и наоборот
        pick = (own or plain or [v for v in voices if v.get("season") != _OPPOSITE.get(wanted or season)])
        if not pick:
            continue
        v = pick[0]
        lines.append({"field": field, "title": block.get("title") or field,
                      "text": v["text"].strip(), "source": v.get("source"), "page": v.get("page")})
        if len(lines) >= MAX_LINES:
            break
    return lines


# Тексты пушей. Названия растений в пуш не подставляем: склонять их по падежам
# надёжно не получится, а «поливать фикуса» хуже, чем без имени.
SEASON_PUSH = {
    "winter": ("Зима на подоконнике",
               "Зимой растениям нужен другой уход. Посмотрите, что советуют книги каждому из них."),
    "spring": ("Весна на подоконнике",
               "Весной растения пересаживают и начинают подкармливать. Посмотрите, что советуют книги."),
    "summer": ("Лето на подоконнике",
               "Летом растения пьют больше. Посмотрите, что советуют книги каждому из них."),
    "autumn": ("Осень на подоконнике",
               "До холодов полив сокращают. Посмотрите, что книги советуют на зиму."),
}
CHECK_PUSH = ("Пора проверить землю",
              "Загляните на подоконник: у каждого растения написано, что книги советуют в этот сезон.")
CHECK_EVERY_DAYS = 14
