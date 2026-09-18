"""Уход за комнатными растениями: извлечение из книг и нормализация.

Зачем модуль. В сентябре 2026 в приложение пришла аудитория, которая снимает не
травы на прогулке, а горшки на подоконнике: 81% снимков за 17 сентября. Корпус
травников ей ответить не может, карточек этих растений в нём нет. Слой ухода
живёт отдельно от карточки гербария и складывается с ней на чтении
(``docs/RFC-houseplants.md``).

Что модуль делает. Из статьи книги про род или вид достаёт утверждения об уходе,
каждое с дословной цитатой и страницей, и переводит книжную фразу в поле, по
которому можно фильтровать и сравнивать источники. Исходная фраза остаётся
рядом с полем: читателю показываем её, а поле нужно машине.

Главное правило. Утверждение без цитаты, которая ДОСЛОВНО есть в скане,
выбрасывается. Модель уже фабриковала рецепты, которых в книге не было
(``docs/RFC-data-quality-llm.md``), а уход читатель выполняет буквально: полил
по выдуманной фразе — залил растение.

Три книги устроены по-разному, и модуль это учитывает:

* Сааков 1985 пишет подробно и оранжерейным языком («переваливают в
  10—12-сантиметровые горшки», «помещают в парник»). Такие фразы помечаются
  ``greenhouse=True`` и в карточку для подоконника не идут.
* Головкин 1989 пишет ссылками на общую часть: «Уход общий. Земельная смесь
  № 2». Такая фраза сохраняется как ссылка и разрешается отдельно, иначе
  читатель получит слова, которые ему ничего не говорят.
* Хессайон и Воронцов пишут готовыми рубриками: температура, свет, полив,
  влажность, пересадка, размножение. Их фразы нормализуются напрямую.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.llm import chat_completion_json

# Книги слоя. Аудитория важна: у Саакова половина советов написана для
# оранжереи, и карточке подоконника они не годятся. Справочник живёт здесь, а
# не в скрипте заливки, потому что им пользуются и заливка, и разбор вводных
# глав, и им же подписан каждый голос в карточке.
HOUSEPLANT_BOOKS: dict[str, dict] = {
    "saakov1985": {
        "title": "Оранжерейные и комнатные растения и уход за ними",
        "author": "Сааков С. Г.", "year": 1985, "audience": "greenhouse",
        "object": "saakov-1985-oranzhereynye-i-komnatnye.pdf",
    },
    "golovkin1989": {
        "title": "Комнатные растения. Справочник",
        "author": "Головкин Б. Н. и др.", "year": 1989, "audience": "room",
        "object": "golovkin-1989-komnatnye-rasteniia-spravochnik.pdf",
    },
    "vorontsov2005": {
        "title": "Уход за комнатными растениями. Практические советы",
        "author": "Воронцов В. В.", "year": 2005, "audience": "room",
        "object": "vorontsov-2005-ukhod-za-komnatnymi-rasteniiami.pdf",
    },
    "hessayon": {
        "title": "Всё о комнатных растениях",
        "author": "Хессайон Д. Г.", "year": 2000, "audience": "room",
        "object": "hessayon-vse-o-komnatnykh-rasteniiakh.pdf",
    },
}

# Поля ухода. Ключ — имя в базе, значение — как поле называется для читателя.
CARE_FIELDS: dict[str, str] = {
    "light": "Свет",
    "water": "Полив",
    "temperature": "Температура",
    "humidity": "Влажность и опрыскивание",
    "soil": "Земля",
    "feeding": "Подкормка",
    "repotting": "Пересадка",
    "propagation": "Размножение",
    "placement": "Где держать",
    "dormancy": "Период покоя",
    "pruning": "Обрезка",
}

# Сезон, к которому относится утверждение. Для полива и температуры он почти
# всегда есть и почти всегда меняет смысл на противоположный.
SEASONS = ("summer", "winter")

# Режимы полива в том виде, в каком о них пишут книги. Числа не выдумываем:
# «обильно» у Хессайона и «обильно» у Саакова могут означать разное, поэтому
# режим остаётся словом, а дни появляются только там, где книга их назвала.
WATER_MODES = {
    "abundant": "обильный",
    "moderate": "умеренный",
    "sparse": "скудный",
    "dry_between": "давать просохнуть между поливами",
    "keep_moist": "держать землю влажной постоянно",
}

_WATER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("keep_moist", re.compile(r"(всё|все) время во влажном|постоянно влажн|не допуска\w+ пересых", re.I)),
    ("dry_between", re.compile(r"просох\w+ между поливами|дайте?\s+(верхнему слою|почве|земле)\s+просохнуть|"
                               r"позвольте поверхности почвы просохнуть", re.I)),
    ("abundant", re.compile(r"обильн", re.I)),
    ("moderate", re.compile(r"умеренн", re.I)),
    ("sparse", re.compile(r"скудн|редк|ограничив|ограничен|умеренно-скудн", re.I)),
]

# Сезонные маркеры. «С весны до осени» у книг означает тёплый сезон роста,
# «в зимний период» — покой. Ничего больше из них не выводим.
_SUMMER_RE = re.compile(r"с весны до осени|весной и летом|летом|в период роста|"
                        r"с весны до поздней осени|в тёплое время|в теплое время", re.I)
_WINTER_RE = re.compile(r"зим\w+|в период покоя|в холодное время", re.I)

# Частота полива, когда книга назвала её прямо. Три формы, которые реально
# встречаются: «раз в 10—14 дней», «два раза в месяц», «ежедневно».
_DAYS_RE = re.compile(r"раз\w*\s+в\s+(\d+)\s*[—–-]\s*(\d+)\s*д", re.I)
_DAYS_ONE_RE = re.compile(r"раз\w*\s+в\s+(\d+)\s*д", re.I)
_PER_MONTH_RE = re.compile(r"(\d+|два|три|четыре)\s+раза?\s+в\s+месяц", re.I)
_PER_WEEK_RE = re.compile(r"(\d+|два|три)\s+раза?\s+в\s+недел", re.I)
_DAILY_RE = re.compile(r"ежедневн|каждый день", re.I)

_NUM_WORDS = {"два": 2, "три": 3, "четыре": 4}

# Температура: «минимум 7°С», «не менее 16 °C», «10—14 °C», «выше 23 °С».
# Книги печатают и латинскую C, и кириллическую С — обе ловим.
# Буква после знака градуса необязательна: Сааков пишет «20—22°» и «до 16°»,
# Хессайон «16°С», Воронцов «8 — 12 °C». Без этого послабления две трети
# температурных фраз оставались без числа.
_TEMP_RANGE_RE = re.compile(r"(\d{1,2})\s*[—–-]\s*(\d{1,2})\s*°\s*[CСc]?", re.I)
_TEMP_MIN_RE = re.compile(r"(?:минимум|не менее|по меньшей мере|не ниже)\s*,?\s*(\d{1,2})\s*°?\s*[CСc]?", re.I)
_TEMP_MAX_RE = re.compile(r"(?:выше|не выше|более|снижают до|до)\s*(\d{1,2})\s*°\s*[CСc]?", re.I)

# Свет — четыре ступени, которыми книги реально пользуются.
# Порядок важен: «слегка тенистое место» и «яркий свет без прямых солнечных
# лучей» обязаны попасть в полутень раньше, чем сработает голое «тенист» или
# «солнечн». Поэтому полутень стоит первой, а тень последней.
_LIGHT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("part_shade", re.compile(r"полутен|слегка тенист|рассеянн\w+ свет|без прямых солнечных|"
                              r"притен|защит\w+ от прямых", re.I)),
    ("sun", re.compile(r"прям\w+ солнечн\w+ (свет|луч)|полное солнце|солнечное место|"
                       r"светолюбив|солнечную сторону", re.I)),
    ("bright", re.compile(r"ярко освещ|хорошо освещ|светл\w+ место|самое светлое|"
                          r"местоположение\w*\s+\w*\s*светл|рекомендуется светл|"
                          r"хорошем освещении|осветлённ|осветленн|в светлых", re.I)),
    ("shade", re.compile(r"тенист|в тени|теневынослив", re.I)),
]

# Оранжерейный язык. Эти слова означают, что совет адресован не квартире, а
# теплице или производству, и в карточку для подоконника он не идёт.
_GREENHOUSE_RE = re.compile(r"оранжере|парник|теплиц|маточн\w+ растени|при массовом размножении|"
                            r"стеллаж\w*\s+оранжере|разводочн", re.I)

# Ссылки на общую часть книги (Головкин). Разрешаются отдельно, по вводным главам.
_REFERENCE_RE = re.compile(r"уход общий|земельная смесь\s*№?\s*(\d+)|смесь\s*№\s*(\d+)", re.I)


@dataclass
class CareFact:
    """Одно утверждение об уходе, привязанное к цитате."""

    field_name: str                  # ключ из CARE_FIELDS
    season: str | None = None        # summer | winter | None
    value: dict = field(default_factory=dict)   # нормализованное значение
    value_text: str = ""             # фраза книги, как её прочитает человек
    quote: str = ""                  # дословная цитата для проверки
    page: int | None = None
    greenhouse: bool = False         # совет про оранжерею, не про комнату
    reference: str | None = None     # «уход общий», «земельная смесь № 2»


@dataclass
class ProblemFact:
    """Вредитель, болезнь или расстройство: признак, причина, что делать."""

    kind: str                        # pest | disease | disorder
    name: str = ""
    symptom: str = ""
    cause: str = ""
    remedy: str = ""
    chemicals: list[str] = field(default_factory=list)
    quote: str = ""
    page: int | None = None


# ---------------------------------------------------------------- нормализация


def normalize_text(raw: str) -> str:
    """Убирает следы распознавания, мешающие сверить цитату со сканом.

    Книги сканированы с переносами: «сте¬\\nлющиеся», «расте-\\nния». Если не
    склеить их обратно, дословная проверка цитаты провалится на ровном месте.
    """
    s = raw.replace("­", "")
    s = re.sub(r"[¬]\s*\n\s*", "", s)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def quote_is_grounded(quote: str, source_text: str) -> bool:
    """Правда ли, что цитата дословно есть в тексте страницы.

    Сравниваем по нормализованному виду и без регистра: распознавание рвёт
    слова переносами, но не переставляет их.
    """
    if not quote or not source_text:
        return False
    q = normalize_text(quote).lower()
    src = normalize_text(source_text).lower()
    if len(q) < 12:          # обрывок в две буквы «найдётся» в любом тексте
        return False
    return q in src


def _season_of(phrase: str) -> str | None:
    """Какому сезону принадлежит фраза. None, если книга сезон не назвала."""
    has_winter = bool(_WINTER_RE.search(phrase))
    has_summer = bool(_SUMMER_RE.search(phrase))
    if has_winter and not has_summer:
        return "winter"
    if has_summer and not has_winter:
        return "summer"
    return None


def _frequency_of(phrase: str) -> dict:
    """Частота в днях, если книга назвала её прямо. Иначе пустой словарь."""
    m = _DAYS_RE.search(phrase)
    if m:
        return {"days_min": int(m.group(1)), "days_max": int(m.group(2))}
    m = _DAYS_ONE_RE.search(phrase)
    if m:
        n = int(m.group(1))
        return {"days_min": n, "days_max": n}
    m = _PER_WEEK_RE.search(phrase)
    if m:
        times = _NUM_WORDS.get(m.group(1).lower(), None) or int(m.group(1))
        return {"days_min": max(1, 7 // times), "days_max": max(1, 7 // times)}
    m = _PER_MONTH_RE.search(phrase)
    if m:
        times = _NUM_WORDS.get(m.group(1).lower(), None) or int(m.group(1))
        return {"days_min": max(1, 30 // times), "days_max": max(1, 30 // times)}
    if _DAILY_RE.search(phrase):
        return {"days_min": 1, "days_max": 1}
    return {}


def split_by_season(phrase: str) -> list[str]:
    """Режет фразу на части, если в ней два режима сразу.

    Книги почти всегда пишут полив одним предложением на два сезона:
    «Поливайте обильно с весны до осени, зимой поливайте умеренно». Разрезаем
    по границе сезона, чтобы каждая часть стала отдельным утверждением.
    """
    text = normalize_text(phrase)
    parts = re.split(r"(?<=[.;])\s+|,\s+(?=зим|летом|весной|с весны)", text)
    return [p.strip() for p in parts if p.strip()]


def normalize_watering(phrase: str) -> list[dict]:
    """Переводит книжную фразу о поливе в режимы по сезонам.

    Возвращает список словарей вида ``{"season": "winter", "mode": "moderate",
    "mode_ru": "умеренный", "text": "<часть фразы>"}``. Дни появляются только
    тогда, когда книга их назвала: выдумывать «раз в неделю» за автора нельзя.
    """
    out: list[dict] = []
    for part in split_by_season(phrase):
        mode = None
        for key, rx in _WATER_PATTERNS:
            if rx.search(part):
                mode = key
                break
        if mode is None:
            continue
        rule = {
            "season": _season_of(part),
            "mode": mode,
            "mode_ru": WATER_MODES[mode],
            "text": part,
        }
        rule.update(_frequency_of(part))
        out.append(rule)
    # Одна и та же часть фразы иногда попадает в два куска разреза; режим и
    # сезон вместе задают утверждение, поэтому дубли по этой паре убираем.
    seen: set[tuple] = set()
    unique: list[dict] = []
    for rule in out:
        key = (rule["season"], rule["mode"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(rule)
    return unique


def normalize_temperature(phrase: str) -> dict:
    """Достаёт градусы: диапазон, нижнюю границу, верхнюю границу."""
    text = normalize_text(phrase)
    out: dict = {}
    m = _TEMP_RANGE_RE.search(text)
    if m:
        out["c_min"], out["c_max"] = int(m.group(1)), int(m.group(2))
    m = _TEMP_MIN_RE.search(text)
    if m and "c_min" not in out:
        out["c_min"] = int(m.group(1))
    m = _TEMP_MAX_RE.search(text)
    if m:
        out["c_max_warn"] = int(m.group(1))
    season = _season_of(text)
    if season:
        out["season"] = season
    return out


def normalize_light(phrase: str) -> dict:
    """Ступень освещённости: солнце, яркий свет, полутень, тень."""
    text = normalize_text(phrase)
    for key, rx in _LIGHT_PATTERNS:
        if rx.search(text):
            return {"level": key}
    return {}


def is_greenhouse_advice(phrase: str) -> bool:
    """Совет адресован оранжерее или производству, а не комнате."""
    return bool(_GREENHOUSE_RE.search(normalize_text(phrase)))


_GLOSSARY_KEY_RE = re.compile(r"земельн\w*\s+смес\w*\s*№?\s*(\d+)", re.I)


def canonical_reference_key(raw: str) -> str | None:
    """Приводит ключ словаря книги к тому виду, в каком его ищут статьи.

    «Земельная смесь №2», «земельная смесь № 2» и «Общий уход за растениями»
    должны совпасть со ссылками, которые извлекатель нашёл в статьях про виды,
    иначе расшифровка никогда к ним не подклеится.
    """
    body = normalize_text(raw).lower()
    if "уход общий" in body or "общий уход" in body:
        return "уход общий"
    match = _GLOSSARY_KEY_RE.search(body)
    if match:
        return f"земельная смесь № {int(match.group(1))}"
    return None


def pick_russian_name(names: list[str]) -> str | None:
    """Выбирает русское имя, которое не стыдно показать в заголовке карточки.

    Книги пишут имя видовой статьи сокращённо («Ф. упругий», «С. спаржевидная»),
    а родовые заголовки набирают прописными («АЛОКАЗИЯ»). Для карточки нужно
    имя рода целиком и обычным регистром, поэтому сокращения отбрасываются, а
    крик переводится в нормальное написание.
    """
    candidates = [n.strip() for n in names if n and n.strip()]
    if not candidates:
        return None

    full = [n for n in candidates if not re.match(r"^[А-ЯЁ]\.\s", n)]
    pool = full or candidates
    # Самое частое написание и есть общепринятое.
    best = max(pool, key=pool.count)
    if best.isupper():
        best = best.capitalize()
    return best


def describe_soil_mix(mix: dict) -> str:
    """Собирает строку таблицы земельных смесей в фразу для читателя.

    Головкин печатает смеси таблицей: восемь номеров и шесть столбцов, где
    прочерк означает, что компонента в смеси нет. Человеку нужна не таблица, а
    состав: «дерновая земля 2, листовая земля 1, перегной 1, торф 1, песок 1».
    """
    parts = [f"{name} {value}" for name, value in (mix.get("parts") or {}).items() if value]
    body = ", ".join(parts)
    note = (mix.get("note") or "").strip()
    if note:
        body = f"{body}, {note.lstrip('+ ').strip()}"
    return f"{body} (части по объёму)" if body else ""


def reference_in(phrase: str) -> str | None:
    """Ссылка на общую часть книги, если фраза её содержит.

    Головкин пишет «Уход общий. Земельная смесь № 2», и без разрешения этих
    ссылок карточка получит слова, за которыми для читателя ничего нет.
    """
    m = _REFERENCE_RE.search(normalize_text(phrase))
    if not m:
        return None
    num = m.group(1) or m.group(2)
    if num:
        return f"земельная смесь № {num}"
    return "уход общий"


# ------------------------------------------------------------- извлечение


# Модель получает статью книги целиком и возвращает утверждения с цитатами.
# Просим её ничего не пересказывать: цитата обязана совпадать со сканом слово
# в слово, иначе утверждение выбрасывается на проверке ниже.
_CARE_PROMPT = """Ты разбираешь страницу из книги про комнатные растения.

Верни СТРОГО JSON:
{
  "plants": [
    {
      "taxon": {"latin": "...", "ru": "...", "rank": "genus|species"},
      "care": [
        {"field": "<одно из: light, water, temperature, humidity, soil, feeding,
                    repotting, propagation, placement, dormancy, pruning>",
         "quote": "<дословный кусок текста, слово в слово, без правок>",
         "text": "<та же мысль, приведённая к читаемому виду: раскрой сокращения,
                   убери следы распознавания, НИЧЕГО не добавляй от себя>"}
      ],
      "problems": [
        {"kind": "pest|disease|disorder",
         "name": "<вредитель, болезнь или расстройство>",
         "symptom": "<как это выглядит на растении>",
         "cause": "<причина, если названа>",
         "remedy": "<что делать, как сказано в книге>",
         "chemicals": ["<препараты, если названы>"],
         "quote": "<дословный кусок текста>"}
      ]
    }
  ]
}

Правила:
1. На странице бывает НЕСКОЛЬКО растений подряд. Верни каждое отдельным
   элементом "plants" и не смешивай их советы между собой.
2. Цитата берётся из текста БЕЗ изменений. Если подходящей цитаты нет,
   утверждение не включай.
3. Ничего не додумывай: ни градусов, ни частоты полива, ни названий, которых
   в тексте нет.
4. Морфологию (какие листья, какие цветки) НЕ извлекай, нужен только уход и
   болезни.
5. Если текст говорит про род целиком, rank = "genus".
6. Латынь бери ТОЛЬКО из этого текста. Распознавание её часто портит: буквы
   подменены цифрами и кириллицей. Восстанавливай написание, но не подставляй
   вид, которого в тексте нет. Если восстановить нельзя, оставь пустую строку.

Текст статьи:
---
{article}
---"""


def _field_value(field_name: str, text: str) -> tuple[dict, str | None]:
    """Нормализованное значение поля и сезон, если он у фразы один.

    Полив может дать несколько правил сразу (лето и зима), поэтому сезон здесь
    возвращается только тогда, когда правило одно; разбор на два утверждения
    делает ``build_care_facts``.
    """
    if field_name == "water":
        rules = normalize_watering(text)
        if len(rules) == 1:
            rule = dict(rules[0])
            season = rule.pop("season", None)
            return rule, season
        return {"rules": rules}, None
    if field_name == "temperature":
        value = normalize_temperature(text)
        season = value.pop("season", None)
        return value, season
    if field_name == "light":
        return normalize_light(text), None
    return {}, None


# Слова, по которым поле определяется надёжнее, чем по выбору модели. Пилот на
# Саакове показал, зачем это нужно: фразу «Зимой поливку ограничивают, однако
# земляной ком не доводят до полной просушки» модель положила в «период покоя»,
# и совет по поливу не попал бы в карточку туда, где его ищут.
_FIELD_MARKERS: dict[str, re.Pattern] = {
    "water": re.compile(r"полив|поливк|поливай|земляной ком|пересуш|просушк", re.I),
    "temperature": re.compile(r"температур|в прохладном месте|содержат зимой при", re.I),
    "soil": re.compile(r"состав земли|земельная смесь|субстрат|дерновая|перегнойн|листов\w+ земл", re.I),
    "feeding": re.compile(r"подкорм|удобрен", re.I),
    "propagation": re.compile(r"размнож|черенк|отводк|делени|посев|укорен|отпрыск|прививк", re.I),
    "repotting": re.compile(r"пересадк|пересажива|перевалива|горшк", re.I),
    "pruning": re.compile(r"обрезк|обрежьте|прищип|укорачива", re.I),
    "humidity": re.compile(r"опрыскива|влажност\w* воздуха|лоток с галькой", re.I),
    "light": re.compile(r"свет|солнечн|тенист|притен|освещ", re.I),
    "placement": re.compile(r"опор|подоконник|на окн|разместит|балкон|вентиляц", re.I),
    "dormancy": re.compile(r"период\w* покоя|покой", re.I),
}

# Порядок разрешения спора: чем конкретнее примета, тем раньше её проверяем.
_FIELD_PRIORITY = ("water", "soil", "feeding", "propagation", "repotting",
                   "pruning", "humidity", "temperature", "light", "placement")


def field_by_content(text: str, proposed: str) -> str:
    """Уточняет поле по самой фразе, но спорит с моделью только при нужде.

    Если у поля, которое назвала модель, примета в тексте есть, выбор модели
    остаётся: абзац «Растения размножают черенками… оптимальная температура
    для укоренения не менее 20°» это размножение, хотя градусы в нём тоже есть.
    Спорим только тогда, когда приметы предложенного поля в тексте нет вовсе:
    так фраза «Зимой поливку ограничивают» уходит из «периода покоя» в полив.
    """
    own = _FIELD_MARKERS.get(proposed)
    if own and own.search(text):
        return proposed
    for field_name in _FIELD_PRIORITY:
        if _FIELD_MARKERS[field_name].search(text):
            return field_name
    return proposed


def classify_sentence(sentence: str, fallback: str) -> str:
    """Поле одного предложения: по приметам, а не по тому, что назвала модель."""
    for field_name in _FIELD_PRIORITY:
        if _FIELD_MARKERS[field_name].search(sentence):
            return field_name
    return fallback


# Длина, начиная с которой кусок подозревается в том, что в нём несколько
# разных советов сразу. Короткие фразы книг («Пересаживайте весной каждые два
# года») режущей машине отдавать незачем.
_LONG_ENOUGH_TO_SPLIT = 200

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def split_into_field_parts(text: str, proposed: str) -> list[tuple[str, str]]:
    """Режет длинный кусок на предложения и раскладывает их по полям.

    Воронцов пишет размещение одним абзацем: там и солнце, и окна, и подпорка
    для стеблей, и зимняя температура. Целиком такой абзац годится только в одно
    поле, а человек ищет в карточке температуру отдельно от света. Режем по
    предложениям, определяем поле каждого и склеиваем соседей с одним полем,
    чтобы мысль не рассыпалась на обрывки.
    """
    body = normalize_text(text)
    if len(body) < _LONG_ENOUGH_TO_SPLIT:
        return [(proposed, body)]

    sentences = [s.strip() for s in _SENTENCE_RE.split(body) if s.strip()]
    if len(sentences) < 2:
        return [(proposed, body)]

    parts: list[tuple[str, str]] = []
    for sentence in sentences:
        # Внутри абзаца каждое предложение судится само по себе. Поле, которое
        # назвала модель, тут не имеет веса: у Воронцова фраза про зимние
        # градусы стоит рядом со словом «балкон», и предпочтение размещению
        # оставило бы температуру ненайденной.
        field_name = classify_sentence(sentence, proposed)
        if parts and parts[-1][0] == field_name:
            parts[-1] = (field_name, f"{parts[-1][1]} {sentence}")
        else:
            parts.append((field_name, sentence))

    # Если всё съехалось в одно поле, резать было незачем.
    if len({f for f, _ in parts}) < 2:
        return [(proposed, body)]
    return parts


def find_quote(fragment: str, source_text: str) -> str:
    """Ищет в скане дословный кусок, соответствующий фразе.

    Фраза приходит из ответа модели, где распознавание уже подчищено, поэтому
    искать её в скане надо не точным совпадением, а по самому длинному общему
    куску. Если нашлось меньше половины фразы, честной цитаты нет.
    """
    from difflib import SequenceMatcher

    needle = normalize_text(fragment)
    haystack = normalize_text(source_text)
    if not needle or not haystack:
        return ""

    matcher = SequenceMatcher(None, haystack.lower(), needle.lower(), autojunk=False)
    match = matcher.find_longest_match(0, len(haystack), 0, len(needle))
    if match.size < max(12, len(needle) // 2):
        return ""
    return haystack[match.a: match.a + match.size].strip()


def build_care_facts(items: list[dict], source_text: str, page: int | None) -> list[CareFact]:
    """Превращает ответ модели в проверенные утверждения.

    Выбрасывает всё, чья цитата не нашлась в тексте страницы: это единственная
    защита от выдуманного совета по уходу.
    """
    facts: list[CareFact] = []
    for item in items:
        proposed = (item.get("field") or "").strip()
        if proposed not in CARE_FIELDS:
            continue
        quote = (item.get("quote") or "").strip()
        if not quote_is_grounded(quote, source_text):
            continue
        text = normalize_text(item.get("text") or quote)
        # Заголовок рубрики («Размножение:») это не совет, а подпись над ним.
        if text.endswith(":") and len(text) < 30:
            continue

        for field_name, part in split_into_field_parts(text, field_by_content(text, proposed)):
            # У каждой части своя цитата: иначе совет про полив ссылался бы на
            # абзац, где про полив нет ни слова.
            part_quote = find_quote(part, source_text) or quote

            if field_name == "water":
                rules = normalize_watering(part)
                if len(rules) > 1:
                    # «Поливайте обильно с весны до осени, зимой умеренно» это
                    # два разных совета: храним их порознь, иначе карточка не
                    # сможет показать человеку тот, который нужен сегодня.
                    for rule in rules:
                        season = rule.pop("season", None)
                        facts.append(CareFact(
                            field_name="water", season=season, value=rule,
                            value_text=rule.get("text", part), quote=part_quote, page=page,
                            greenhouse=is_greenhouse_advice(part),
                            reference=reference_in(part),
                        ))
                    continue

            value, season = _field_value(field_name, part)
            facts.append(CareFact(
                field_name=field_name, season=season, value=value, value_text=part,
                quote=part_quote, page=page, greenhouse=is_greenhouse_advice(part),
                reference=reference_in(part),
            ))
    return facts


def build_problem_facts(items: list[dict], source_text: str, page: int | None) -> list[ProblemFact]:
    """То же для болезней и вредителей: без дословной цитаты факт не живёт."""
    out: list[ProblemFact] = []
    for item in items:
        quote = (item.get("quote") or "").strip()
        if not quote_is_grounded(quote, source_text):
            continue
        name = normalize_text(item.get("name") or "")
        if not name:
            # Безымянная запись читателю ничего не говорит: в справочнике
            # болезней он ищет по имени вредителя или по признаку.
            continue
        kind = (item.get("kind") or "").strip()
        if kind not in ("pest", "disease", "disorder"):
            kind = "disorder"
        out.append(ProblemFact(
            kind=kind,
            name=name,
            symptom=normalize_text(item.get("symptom") or ""),
            cause=normalize_text(item.get("cause") or ""),
            remedy=normalize_text(item.get("remedy") or ""),
            chemicals=[normalize_text(c) for c in (item.get("chemicals") or []) if c],
            quote=quote,
            page=page,
        ))
    return out


def verify_latin(latin: str, russian: str, source_text: str) -> str:
    """Оставляет латынь, только если она подтверждена текстом или русским именем.

    Пилот на Воронцове показал, зачем: модель приписала свинчатке латынь
    замиокулькаса, взяв её из примера в задании. Такая подмена страшнее пустого
    поля, потому что карточка ухода уедет не тому растению.

    Подтверждений два, и любого хватает. Первое: имя рода встречается в самом
    тексте, пусть и с выбитыми буквами, поэтому сравниваем без пробелов и без
    кириллических двойников. Второе: имя рода похоже на русское имя из той же
    статьи («Замиокулькас» и Zamioculcas), а это как раз тот случай, когда
    распознавание разнесло латынь в труху, но человек всё равно узнаёт растение.
    """
    genus = (latin or "").strip().split(" ")[0]
    if len(genus) < 4:
        return ""

    folded_source = re.sub(r"[\s.]", "", source_text.translate(_HOMOGLYPHS)).lower()
    if genus.lower() in folded_source:
        return latin.strip()

    if russian:
        target = transliterate(russian.split()[0])
        if len(target) >= 4 and _similarity(genus, target) >= 0.75:
            return latin.strip()

    return ""


def merge_plant_blocks(blocks: list[dict]) -> list[dict]:
    """Склеивает куски про одно и то же растение.

    Воронцов печатает уход и болезни в разных колонках разворота, и модель
    вернула их двумя записями: «плюмбаго» и «свинчатка». Это одно растение,
    у него просто два русских имени, поэтому блоки сводятся по латыни, а при
    её отсутствии по русскому имени.
    """
    merged: dict[str, dict] = {}
    for block in blocks:
        taxon = block.get("taxon") or {}
        key = (taxon.get("latin") or taxon.get("ru") or "").strip().lower()
        if not key:
            key = f"без имени {len(merged)}"
        if key not in merged:
            merged[key] = block
            continue
        target = merged[key]
        seen = {(f.field_name, f.season, f.quote) for f in target["care"]}
        target["care"].extend(f for f in block["care"]
                              if (f.field_name, f.season, f.quote) not in seen)
        seen_problems = {(p.kind, p.name) for p in target["problems"]}
        target["problems"].extend(p for p in block["problems"]
                                  if (p.kind, p.name) not in seen_problems)
    return list(merged.values())


def normalize_latin(latin: str) -> str:
    """Приводит написание к обычному: род с большой буквы, вид с маленькой.

    Книги печатают заголовки прописными («TRICHOCAULON»), и без этого один род
    попал бы в базу под двумя разными написаниями. Заодно чиним кириллические
    двойники внутри латинского слова: распознавание даёт «Aglaoнema» и
    «Pelargoniуm», где на глаз всё верно, а справочник такое имя не находит.
    """
    cleaned = latin.strip()
    if re.search(r"[A-Za-z]", cleaned) and re.search(r"[А-Яа-яЁё]", cleaned):
        cleaned = cleaned.translate(_HOMOGLYPHS_LOWER).translate(_HOMOGLYPHS)
    parts = [p for p in re.split(r"\s+", cleaned) if p]
    if not parts:
        return ""
    head = parts[0].capitalize()
    tail = [p.lower() if p.isupper() else p for p in parts[1:]]
    return " ".join([head, *tail])


async def extract_article(article: str, page: int | None = None,
                          fallback_latin: str = "") -> list[dict]:
    """Разбирает кусок книги: какие растения на нём есть, их уход и болезни.

    Возвращает список ``{"taxon": {...}, "care": [CareFact], "problems":
    [ProblemFact]}`` — по элементу на растение. Список, а не одно растение,
    потому что у Хессайона на одной странице их идёт по три подряд (юкка,
    замиокулькас, зебрина), и первый прогон пилота потерял два из трёх.

    Всё, что не подтвердилось дословной цитатой из этого же текста, отброшено.
    """
    source_text = normalize_text(article)
    prompt = _CARE_PROMPT.replace("{article}", article)
    data = await chat_completion_json(
        [{"role": "user", "content": prompt}],
        task="plant_extraction",   # qwen3-235b-2507: та же модель, что разбирает травники
        temperature=0.1,
    )
    if not isinstance(data, dict):
        return []

    blocks = data.get("plants")
    if not isinstance(blocks, list):
        # Модель иногда сваливается к одному растению без обёртки.
        blocks = [data] if data.get("taxon") else []

    out: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        care = build_care_facts(block.get("care") or [], source_text, page)
        problems = build_problem_facts(block.get("problems") or [], source_text, page)
        if not care and not problems:
            continue
        taxon = dict(block.get("taxon") or {})
        latin = verify_latin(taxon.get("latin") or "", taxon.get("ru") or "", source_text)
        if not latin and fallback_latin:
            # Сааков сокращает видовые имена до буквы: «С. обильноцветущий».
            # Латыни у такой статьи нет, но род известен из её заголовка, и
            # уход честнее положить на род, чем выбросить.
            latin = fallback_latin
            taxon["rank"] = "genus"
        taxon["latin"] = normalize_latin(latin)
        out.append({"taxon": taxon, "care": care, "problems": problems})
    return merge_plant_blocks(out)


# --------------------------------------------------------------- нарезка книги


# Родовая статья у Саакова начинается строкой «Род HOYA R. Br. — ХОЙЯ». Ловим
# её по слову «Род», а имя разбираем отдельно: заголовки набраны вразрядку, и
# распознавание рвёт латынь пробелами («H Y P O E ST E S», «JACOBI NIA») и
# подменяет латинские буквы похожими кириллическими. Строгий шаблон на этом
# терял каждый шестой род, включая хойю, диффенбахию и сингониум.
# Само слово «Род» распознавание тоже разрывает: «Р од SANSEVIERIA Thunb.».
_GENUS_LINE_RE = re.compile(r"^[ \t]*Р\s?о\s?д\s+(\S.*)$", re.M)

# Кириллические двойники латинских букв: в заголовке вразрядку распознавание
# сваливается на них постоянно.
# Строчные двойники подобраны по тому, что распознавание путает на деле, а не
# по сходству начертаний: «н» встаёт на место «n» («Aglaoнema»), хотя на глаз
# эти буквы разные. Заглавная «Н» при этом остаётся латинской «H».
_HOMOGLYPHS_LOWER = str.maketrans({
    "а": "a", "е": "e", "к": "k", "м": "m", "н": "n", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j",
})

_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J",
})


def fold_genus_name(raw: str) -> str:
    """Собирает разорванное имя рода: убирает пробелы и правит двойников букв.

    «H Y P O E ST E S» превращается в «Hypoestes». Ошибки распознавания внутри
    слова («CROSSAN0RA») тут не лечатся: имя проверяется по GBIF отдельно, тем
    же путём, каким чинится латынь в гербарии.
    """
    folded = raw.translate(_HOMOGLYPHS).replace(" ", "").replace(".", "")
    letters = re.sub(r"[^A-Za-z\-]", "", folded)
    return letters.capitalize()


# Грубая транслитерация русского имени рода в латиницу. Нужна не для красоты,
# а чтобы выбрать правильную границу имени в разорванном заголовке: русское имя
# стоит в той же строке и знает, где имя кончилось, а автор начался.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(word: str) -> str:
    """«Монстера» превращается в «monstera», «Хойя» в «hoiya»."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in word.lower())


def _similarity(latin: str, russian_translit: str) -> float:
    """Насколько латинское написание похоже на транслитерацию русского имени."""
    from difflib import SequenceMatcher

    a = latin.lower()
    # Латынь и русская традиция расходятся предсказуемо: h читается как «г»,
    # y как «и», c как «к», j как «я»/«й». Сводим их к одному виду, иначе
    # «HYPOESTES» и «гипоэстес» окажутся непохожими.
    for src, dst in (("h", "g"), ("y", "i"), ("c", "k"), ("j", "i"), ("ph", "f")):
        a = a.replace(src, dst)
    b = russian_translit.replace("h", "g").replace("y", "i").replace("c", "k")
    return SequenceMatcher(None, a, b).ratio()


def parse_genus_header(line: str) -> tuple[str, str]:
    """Из строки заголовка достаёт латинское имя рода и русское.

    Имя набрано прописными, фамилия автора идёт следом («ACANTHUS L.»,
    «JACOBINIA Nees ex Moric.»), но распознавание рвёт заголовок как попало:
    «HOY A R. Br.», «H Y P O E ST E S Soland.», «MONSTERA SCHOTT». Правила
    «до первой строчной буквы» не хватает, потому что автор бывает набран
    прописными, а имя бывает разорвано на буквы.

    Поэтому граница выбирается по русскому имени из той же строки: пробуем
    каждую границу и берём ту, где латынь и транслитерация русского имени
    похожи больше всего. «Монстера» отсекает «SCHOTT», «Хойя» склеивает
    «HOY» и «A», «Якобиния» склеивает «JACOBI» и «NIA».
    """
    parts = re.split(r"\s[—–-]\s", line, maxsplit=1)
    left = parts[0]
    russian = parts[1].strip().capitalize() if len(parts) > 1 else ""

    # Имя кончается там, где начался автор. Автора выдают две приметы: слово со
    # строчными буквами («Schott», «Don») и слово с точкой («L.», «R.»). Буквы
    # без точки — это разрядка внутри имени, их резать нельзя.
    tokens: list[str] = []
    for token in left.split():
        if re.search(r"[a-zа-яё]", token) or token.endswith("."):
            break
        tokens.append(token)
    if not tokens:
        return "", russian

    whole_name = fold_genus_name(" ".join(tokens))
    candidates = [fold_genus_name(" ".join(tokens[:n])) for n in range(1, len(tokens) + 1)]
    candidates = [c for c in candidates if len(c) >= 3]
    if not candidates:
        return "", russian

    # Русское имя помогает там, где автор набран прописными и точки не имеет
    # («MONSTERA SCHOTT»). Но доверять ему можно не всегда: у хойи Сааков пишет
    # «Плющ восковидный», и родства с латынью там нет вовсе. Поэтому подсказка
    # перебивает разбор, только если сходство действительно высокое.
    target = transliterate(russian.split()[0]) if russian else ""
    if len(target) >= 4:          # «Ги 10эстес» после распознавания даёт «ги» — такому не верим
        scored = [(round(_similarity(c, target), 3), -len(c), c) for c in candidates]
        best_score, _, best = max(scored)
        # При равном сходстве берём короткое написание: лишний хвост это автор
        # («SANSEVIERIA T» от «Thunb.»), а не часть имени.
        if best_score >= 0.6:
            return best, russian

    return whole_name, russian


@dataclass
class BookArticle:
    """Кусок книги про один род: заголовок, текст и страница, где он начался."""

    genus_latin: str
    genus_ru: str
    text: str
    page: int


def find_genus_articles(pages: list[str]) -> list[BookArticle]:
    """Режет книгу на родовые статьи по заголовкам.

    Принимает страницы в порядке книги (индекс + 1 = номер страницы) и
    возвращает статьи от заголовка до следующего заголовка. Резать по
    заголовкам, а не по страницам, обязательно: уход у Саакова стоит в конце
    родовой статьи, и разрез посреди страницы оставил бы полив в одном куске,
    а землю в другом.
    """
    # Сшиваем книгу в одну строку, запоминая, где начинается каждая страница,
    # чтобы потом сказать, на какой странице стоял заголовок.
    offsets: list[int] = []
    cursor = 0
    for page_text in pages:
        offsets.append(cursor)
        cursor += len(page_text) + 1
    whole = "\n".join(pages)

    def page_of(position: int) -> int:
        low, high = 0, len(offsets) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if offsets[middle] <= position:
                low = middle
            else:
                high = middle - 1
        return low + 1

    matches = list(_GENUS_LINE_RE.finditer(whole))
    articles: list[BookArticle] = []
    for index, match in enumerate(matches):
        latin, russian = parse_genus_header(match.group(1))
        if len(latin) < 3:          # строка «Род» без имени — не заголовок
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(whole)
        articles.append(BookArticle(
            genus_latin=latin,
            genus_ru=russian,
            text=whole[match.start():end].strip(),
            page=page_of(match.start()),
        ))
    return articles


def article_has_care(article: BookArticle) -> bool:
    """Есть ли в статье хоть одно утверждение об уходе.

    Нужна, чтобы не гонять модель по статьям, где книга описала только
    морфологию и распространение: у Саакова таких много.
    """
    text = normalize_text(article.text)
    markers = (
        r"состав земли", r"размножа", r"полив", r"пересажива", r"перевалива",
        r"температур", r"опрыскива", r"подкорм", r"удобрен", r"земельная смесь",
        r"уход общий", r"содерж\w+ зимой",
    )
    return any(re.search(m, text, re.I) for m in markers)


# ------------------------------------------------------- сверка имени с GBIF


GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
GBIF_SEARCH_URL = "https://api.gbif.org/v1/species/search"
# Бэкбон GBIF: сводная таксономия, по которой и надо спрашивать имя.
GBIF_BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"

# Имена сверяются пачками и повторяются от статьи к статье, поэтому ответы
# держим в памяти процесса: на книгу это сотни запросов вместо тысяч.
_GBIF_CACHE: dict[str, tuple[str, bool]] = {}

# Ниже этой уверенности GBIF угадывает, а не узнаёт, и его ответ не лучше
# нашего распознавания.
_GBIF_MIN_CONFIDENCE = 92


def read_gbif_match(data: dict, asked: str) -> tuple[str, bool]:
    """Разбирает ответ GBIF: какое имя принять и можно ли ему верить.

    Три случая, и все встретились на живых данных.

    GBIF узнал имя и считает его принятым — берём его написание, оно и есть
    канон. GBIF узнал имя, но считает синонимом («Zebrina pendula») — берём
    принятое имя того же ранга, иначе зебрина никогда не склеится с
    традесканцией, и книги разойдутся по двум карточкам одного растения.
    GBIF имя не узнал вовсе («Stepttanotis» и даже «Stefanotis») — оставляем
    как есть и честно помечаем непроверенным: нечёткий поиск у GBIF тут тоже
    молчит, а выдумывать имя за него нельзя.
    """
    canonical = data.get("canonicalName") or ""
    confidence = data.get("confidence") or 0
    kingdom = data.get("kingdom") or ""
    rank = (data.get("rank") or "").lower()
    status = (data.get("status") or "").upper()
    match_type = (data.get("matchType") or "").upper()
    asked_is_genus = len(asked.split()) == 1

    # «Совпало до высшего ранга» выглядит уверенно, но означает обратное: у
    # Duvalia справочник отвечает canonicalName = «Plantae» с уверенностью 96,
    # и принять такой ответ значит переименовать род в царство.
    if match_type in ("HIGHERRANK", "NONE"):
        return asked, False
    if not canonical or kingdom != "Plantae" or rank not in ("genus", "species"):
        return asked, False
    if confidence < _GBIF_MIN_CONFIDENCE:
        return asked, False
    if asked_is_genus != (rank == "genus"):
        return asked, False

    if status == "SYNONYM":
        accepted = data.get("genus") if asked_is_genus else data.get("species")
        if accepted:
            return normalize_latin(accepted), True
        return asked, False

    return normalize_latin(canonical), True


# Хвосты, которые книга приписывает к имени: сорт в кавычках, подвид,
# разновидность, форма. Для сверки они лишние — справочник знает вид.
_NAME_TAIL_RE = re.compile(r"\s+(?:ssp\.?|subsp\.?|var\.?|f\.|cv\.?)\s+.*$|\s*['‘’“”].*$", re.I)


def latin_for_lookup(name: str) -> str:
    """Готовит имя к сверке: род и вид, без сорта, подвида, автора и знака гибрида.

    «Ctenanthe oppenheimiana 'variegata'» и «Ceropegia linearis ssp. woodii»
    справочник как есть не знает, а их вид знает прекрасно.

    Отдельная забота — фамилия автора, которую книга печатает сразу за именем:
    «Dianella Lam.», «Persea Mill.», «Callicarpa l.». В первом прогоне на этом
    споткнулись два десятка обычных родов. Эпитет вида всегда со строчной буквы
    и без точки, поэтому второе слово с точкой или с заглавной буквы — автор.
    """
    cleaned = _NAME_TAIL_RE.sub("", normalize_latin(name)).strip()
    cleaned = cleaned.replace("×", " ").replace("(", " ").replace(")", " ")
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return ""
    if len(parts) > 1:
        second = parts[1]
        if "." in second or second[:1].isupper() or len(second) < 3:
            return parts[0]
    return " ".join(parts[:2])


async def resolve_latin(name: str) -> tuple[str, bool]:
    """Принятое написание имени и признак того, что GBIF его подтвердил.

    Распознавание книг портит латынь: в базу приехал «Stepttanotis» вместо
    Stephanotis, и без сверки один род расползётся по нескольким написаниям, а
    карточка покажет половину того, что книги про него говорят. GBIF тут тот же
    внешний судья, каким мы уже чиним латынь в гербарии.

    Молчание сети ничего не портит: имя остаётся как было, но непроверенным.
    """
    import httpx

    key = latin_for_lookup(name)
    if not key:
        return "", False
    if key in _GBIF_CACHE:
        return _GBIF_CACHE[key]

    # Царство и ранг обязательны. Без них справочник отказывается выбирать
    # между растением и животным у имён-омонимов и молча отвечает «не знаю»:
    # так у нас «не узнались» Plumbago и Duvalia, вполне обычные комнатные.
    params = {
        "name": key,
        "kingdom": "Plantae",
        "rank": "GENUS" if len(key.split()) == 1 else "SPECIES",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(GBIF_MATCH_URL, params=params)
            data = response.json()
    except Exception:
        return key, False          # сеть подвела — имя не испорчено, но и не проверено

    result = read_gbif_match(data, key)

    # Запасной путь. Сверка именем отказывается узнавать род, если такое же имя
    # носит животное: на «Fittonia» и «Streptocarpus» она отвечает «совпало до
    # типа» и «совпало до царства». Поиск по тому же бэкбону отвечает прямо, и
    # среди ответов мы берём только точное совпадение написания у растения.
    if not result[1]:
        found = await _search_backbone(key, params["rank"])
        if found:
            result = (found, True)

    _GBIF_CACHE[key] = result
    return result


async def _search_backbone(name: str, rank: str) -> str:
    """Ищет имя в бэкбоне GBIF и возвращает точное совпадение у растений."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(GBIF_SEARCH_URL, params={
                "q": name, "rank": rank, "status": "ACCEPTED",
                "datasetKey": GBIF_BACKBONE, "limit": 5,
            })
            data = response.json()
    except Exception:
        return ""

    for row in data.get("results", []):
        canonical = row.get("canonicalName") or ""
        if (row.get("kingdom") == "Plantae"
                and canonical.lower() == name.lower()):
            return normalize_latin(canonical)
    return ""


# ----------------------------------------------------- связь с определителем


async def care_for_latins(db, latins: list[str]) -> dict[str, dict]:
    """Для каждого определённого вида говорит, есть ли для него уход.

    Определитель отвечает «вид определён, но он вне рамок травника» примерно на
    семи отказах из десяти, и почти все они — комнатные. Теперь на такой ответ
    есть что добавить: сколько утверждений об уходе мы знаем и из каких книг.

    Ключ ответа — та латынь, о которой спросили. Значение несёт имя, под
    которым уход записан (вид или его род), чтобы клиент открыл карточку сразу
    по нужному адресу.
    """
    from sqlalchemy import text as sql_text

    wanted: dict[str, tuple[str, str]] = {}      # запрошенное имя → (вид, род)
    for latin in latins:
        lookup = latin_for_lookup(latin)
        if lookup:
            wanted[latin] = (lookup, lookup.split(" ")[0])
    if not wanted:
        return {}

    names = sorted({n for pair in wanted.values() for n in pair})
    rows = (await db.execute(sql_text("""
        SELECT c.taxon_latin, count(*) AS facts,
               array_agg(DISTINCT s.title) AS books
        FROM houseplant_care c
        JOIN houseplant_source s ON s.id = c.source_id
        WHERE c.taxon_latin = ANY(:names) AND c.latin_verified = true
          AND c.greenhouse = false
        GROUP BY c.taxon_latin
    """), {"names": names})).all()
    known = {r.taxon_latin: {"facts": r.facts, "books": list(r.books or [])} for r in rows}

    out: dict[str, dict] = {}
    for latin, (species, genus) in wanted.items():
        # Вид точнее рода, поэтому он и проверяется первым.
        for name, scope in ((species, "species"), (genus, "genus")):
            found = known.get(name)
            if found:
                out[latin] = {"latin": name, "scope": scope,
                              "facts": found["facts"], "books": found["books"]}
                break
    return out
