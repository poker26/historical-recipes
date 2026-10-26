"""Грибы: опасные двойники и уровни, которые модель решать не должна.

Двойника грибу раньше писала модель, которая ставит уровень опасности. Определения
ей не давали, и она отвечала как придётся: у 18 из 68 грибов «двойником» стояла
бледная поганка (у строчка осеннего, у паутинника, у лимонного мухомора), у трёх
буквальное слово «null», а 48 «двойников» висели на самих ядовитых грибах, где
вопрос «какой у него ядовитый двойник» смысла не имеет (замер 26.10.2026).

Теперь двойник берётся только из справочника классических пар путаницы. Вопрос
разный для двух сторон пары:

* у съедобного гриба ``deadly_twin``, ядовитый гриб, похожий на него
  (шампиньон и бледная поганка, сморчок и строчок);
* у ядовитого гриба ``confused_with``, съедобные грибы, за которые его принимают,
  в творительном падеже с предлогом («со сморчками», «с летним опёнком»), чтобы
  подпись читалась фразой «Его путают со сморчками».

Гриба нет в справочнике, значит двойника у него в карточке нет.

Уровни. Список смертельных родов в ``edible_safety`` поднимал до «смертельно» весь
род целиком, и кесарев гриб, серо-розовый мухомор, поплавки и колпак кольчатый
стояли смертельно ядовитыми. Мухоморы и паутинники здесь расписаны по видам. Вид
такого рода, которого в списке нет, получает «нет данных» с объяснением, что в роде
есть смертельные виды: это правда, а «смертельно» про неизвестный вид неправда.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LABELS = {0: "нет данных", 1: "съедобно", 2: "условно съедобно", 3: "ядовито",
          4: "смертельно ядовито"}

# ── двойники съедобных грибов (ключ: вид или род по латыни, строчными) ──
_SHAMPIGNON_POISON = ("agaricus xanthodermus", "agaricus moelleri", "agaricus placomyces")
DEADLY_TWIN = {
    "agaricus": "бледная поганка",
    "russula aeruginea": "бледная поганка",
    "russula virescens": "бледная поганка",
    "russula heterophylla": "бледная поганка",
    "russula cyanoxantha": "бледная поганка",
    "russula ionochlora": "бледная поганка",
    "tricholoma equestre": "бледная поганка",
    "tricholoma portentosum": "рядовка тигровая",
    "tricholoma terreum": "рядовка тигровая",
    "armillaria": "ложноопёнок серно-жёлтый",
    "kuehneromyces mutabilis": "галерина окаймлённая",
    "hypholoma capnoides": "ложноопёнок серно-жёлтый",
    "marasmius oreades": "говорушка беловатая",
    "calocybe gambosa": "энтолома ядовитая",
    "entoloma clypeatum": "энтолома ядовитая",
    "morchella": "строчок обыкновенный",
    "macrolepiota": "мухомор пантерный",
    "chlorophyllum rhacodes": "мухомор пантерный",
    "amanita rubescens": "мухомор пантерный",
    "amanita excelsa": "мухомор пантерный",
    "amanita vaginata": "бледная поганка",
    "amanita fulva": "бледная поганка",
    "amanita crocea": "бледная поганка",
    "amanita battarrae": "бледная поганка",
    "amanita ceciliae": "бледная поганка",
    # взрослый кесарев гриб путают с красным мухомором, у которого дождь смыл
    # хлопья; с бледной поганкой схоже только «яйцо», об этом говорит обоснование
    "amanita caesarea": "мухомор красный",
    "amanita caesareoides": "мухомор красный",
    "cortinarius caperatus": "ядовитые паутинники",
    "lycoperdon": "ложнодождевик обыкновенный",
    "bovista": "ложнодождевик обыкновенный",
    "calvatia": "ложнодождевик обыкновенный",
}
# Родовую карточку сыроежек двойник получает, только если она и правда про
# сыроежки: на латынь Russula в атласе сели и «Рыжики», и «подосиновики».
_GENUS_TWIN_BY_NAME = {"russula": ("сыроежк", "бледная поганка")}

# ── за какие съедобные грибы принимают ядовитые ──
CONFUSED_WITH = {
    "amanita phalloides": "с шампиньонами, зеленушкой и зелёными сыроежками",
    "amanita virosa": "с шампиньонами и белыми поплавками",
    "amanita verna": "с шампиньонами и белыми поплавками",
    "amanita ocreata": "с шампиньонами и белыми поплавками",
    "amanita bisporigera": "с шампиньонами и белыми поплавками",
    "amanita pantherina": "с серо-розовым мухомором и грибом-зонтиком",
    "gyromitra esculenta": "со сморчками",
    "gyromitra gigas": "со сморчками",
    "galerina": "с летним опёнком",
    "hypholoma fasciculare": "с опятами",
    "entoloma sinuatum": "с майским грибом и серыми рядовками",
    "clitocybe dealbata": "с луговым опёнком",
    "clitocybe rivulosa": "с луговым опёнком",
    "clitocybe candicans": "с луговым опёнком",
    "rubroboletus satanas": "с дубовиками",
    "tylopilus felleus": "с белым грибом и подберёзовиком",
    "scleroderma": "с дождевиками",
    "agaricus xanthodermus": "со съедобными шампиньонами",
    "agaricus moelleri": "со съедобными шампиньонами",
    "agaricus placomyces": "со съедобными шампиньонами",
    "tricholoma pardinum": "с серой и землистой рядовками",
    "inocybe erubescens": "с майским грибом",
    "inocybe patouillardii": "с майским грибом",
    "omphalotus": "с лисичками и опятами",
    "lepiota": "с молодыми грибами-зонтиками",
}

# ── уровни по виду или роду ──
_POPLAVOK = ("Поплавки условно съедобны и годятся в пищу только после варки, сырыми они "
             "вызывают расстройство желудка. Собирать их опасно: у бледной поганки тоже "
             "есть мешок-вольва у основания ножки, а кольцо на её ножке с возрастом может "
             "исчезнуть.")
_CORT_EDIBLE = ("Этот паутинник считается условно съедобным и годится в пищу только после "
                "варки. Среди паутинников есть смертельно ядовитые виды, а различить их на "
                "глаз трудно, поэтому собирать паутинники стоит только опытным грибникам.")
_CORT_DEADLY = ("Содержит орелланин, яд, который разрушает почки. Первые признаки отравления "
                "появляются через несколько дней после еды, иногда через две-три недели, "
                "когда почки уже повреждены.")
_AMANITIN = ("Содержит аманитины, яды, которые разрушают печень. Взрослого человека может "
             "убить один гриб, а признаки отравления появляются только через 6–24 часа.")
_PUFFBALL = ("Дождевики и головачи съедобны, пока мякоть внутри белая и плотная; пожелтевшие "
             "и потемневшие грибы не едят. Молодой мухомор в общем покрывале тоже похож на "
             "белый шарик, поэтому каждый гриб разрезают вдоль и смотрят, нет ли внутри "
             "очертаний шляпки и ножки.")
_MIXED_GENUS = {
    "amanita": ("В роде мухоморов есть смертельно ядовитые виды. Про этот вид в книгах "
                "нет надёжных данных, поэтому есть его нельзя."),
    "cortinarius": ("Среди паутинников есть смертельно ядовитые виды. Про этот вид в "
                    "книгах нет надёжных данных, поэтому есть его нельзя."),
    "leucoagaricus": ("Среди мелких лепиот и белошампиньонов есть смертельно ядовитые "
                      "виды. Про этот вид в книгах нет надёжных данных, поэтому есть его "
                      "нельзя."),
}


def _r(level, rationale, edible=(), dangerous=()):
    return {"level": level, "rationale": rationale,
            "edible_parts": list(edible), "dangerous_parts": list(dangerous)}


LEVEL_RULES = {
    # мухоморы
    "amanita phalloides": _r(4, _AMANITIN),
    "amanita virosa": _r(4, _AMANITIN),
    "amanita verna": _r(4, _AMANITIN),
    "amanita ocreata": _r(4, _AMANITIN),
    "amanita bisporigera": _r(4, _AMANITIN),
    "amanita exitialis": _r(4, _AMANITIN),
    "amanita pantherina": _r(4, "Смертельно ядовит: иботеновой кислоты "
                                "и мусцимола в нём больше, чем в красном мухоморе. "
                                "Отравление начинается с бреда и судорог и может кончиться "
                                "смертью."),
    "amanita muscaria": _r(3, "Ядовит: иботеновая кислота и мусцимол "
                              "вызывают бред, судороги и потерю сознания. Смертельные "
                              "отравления редки, но бывают."),
    "amanita regalis": _r(3, "Ядовит так же, как мухомор красный: иботеновая "
                             "кислота и мусцимол вызывают бред, судороги и потерю сознания."),
    "amanita gemmata": _r(3, "Ядовит: в нём есть иботеновая кислота, "
                             "та же, что в красном мухоморе."),
    "amanita citrina": _r(3, "Ядовит, в пищу его не употребляют."),
    "amanita porphyria": _r(3, "Ядовит, в пищу его не употребляют."),
    "amanita caesarea": _r(1, "Кесарев гриб съедобен и считается деликатесом. Взрослый гриб "
                              "похож на красный мухомор, с которого дождь смыл белые хлопья, "
                              "но у кесарева гриба жёлтые пластинки и ножка. Молодой гриб в "
                              "белом покрывале похож на «яйцо» бледной поганки, поэтому такие "
                              "грибы разрезают вдоль: у кесарева внутри видна оранжевая "
                              "шляпка.", edible=["плодовое тело"]),
    "amanita caesareoides": _r(1, "Мухомор цезаревидный съедобен, как и кесарев гриб. "
                                  "Взрослый гриб похож на красный мухомор, с которого дождь "
                                  "смыл белые хлопья, но пластинки и ножка у него жёлтые.", edible=["плодовое тело"]),
    "amanita rubescens": _r(2, "Мухомор серо-розовый съедобен только после варки. В сыром "
                               "грибе есть вещество, которое разрушает красные кровяные "
                               "тельца, а при нагреве оно распадается. Его легко спутать с "
                               "мухомором пантерным, у которого мякоть на изломе не "
                               "краснеет.", edible=["плодовое тело после варки"],
                            dangerous=["сырой гриб"]),
    "amanita excelsa": _r(2, "Мухомор толстый считается условно съедобным, но ценности не "
                             "имеет. Его легко спутать с мухомором пантерным, поэтому "
                             "собирать его не советуют.",
                          edible=["плодовое тело после варки"]),
    "amanita vaginata": _r(2, _POPLAVOK, edible=["плодовое тело после варки"]),
    "amanita fulva": _r(2, _POPLAVOK, edible=["плодовое тело после варки"]),
    "amanita crocea": _r(2, _POPLAVOK, edible=["плодовое тело после варки"]),
    "amanita battarrae": _r(2, _POPLAVOK, edible=["плодовое тело после варки"]),
    "amanita ceciliae": _r(2, _POPLAVOK, edible=["плодовое тело после варки"]),
    # паутинники
    "cortinarius orellanus": _r(4, _CORT_DEADLY),
    "cortinarius rubellus": _r(4, _CORT_DEADLY),
    "cortinarius speciosissimus": _r(4, _CORT_DEADLY),
    "cortinarius orellanoides": _r(4, _CORT_DEADLY),
    "cortinarius gentilis": _r(4, _CORT_DEADLY),
    "cortinarius limonius": _r(4, _CORT_DEADLY),
    "cortinarius splendens": _r(4, _CORT_DEADLY),
    "cortinarius caperatus": _r(1, "Колпак кольчатый съедобен. Его путают с ядовитыми "
                                   "паутинниками, поэтому собирать его стоит только тем, кто "
                                   "хорошо его знает.",
                                edible=["плодовое тело"]),
    "cortinarius praestans": _r(1, "Паутинник превосходный съедобен. Среди паутинников есть "
                                   "смертельно ядовитые виды, поэтому собирать его стоит "
                                   "только опытным грибникам.", edible=["плодовое тело"]),
    "cortinarius varius": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius armillatus": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius mucosus": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius collinitus": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius triumphans": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius alboviolaceus": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius violaceus": _r(2, _CORT_EDIBLE, edible=["плодовое тело после варки"]),
    "cortinarius infractus": _r(0, "Паутинник надломленный несъедобен из-за сильной горечи."),
    "cortinarius traganus": _r(0, "Паутинник козлиный несъедобен: у него резкий неприятный "
                                  "запах. О ядовитости данных мало."),
    # дождевики и головачи
    "lycoperdon": _r(1, _PUFFBALL, edible=["молодые грибы с белой мякотью"]),
    "calvatia": _r(1, _PUFFBALL, edible=["молодые грибы с белой мякотью"]),
    "bovista": _r(1, _PUFFBALL, edible=["молодые грибы с белой мякотью"]),
    "pleurotus": _r(1, "Вешенки съедобны, их выращивают и собирают в лесу.",
                    edible=["плодовое тело"]),
    # прочие ошибки модели
    "armillaria": _r(2, "Опята съедобны только после варки: сырые или недоваренные они "
                        "вызывают расстройство желудка.", edible=["шляпки после варки"],
                     dangerous=["сырые грибы"]),
    "entoloma clypeatum": _r(2, "Энтолома садовая съедобна после отваривания. Её легко "
                                "спутать с энтоломой ядовитой, которая очень на неё похожа.",
                             edible=["плодовое тело после варки"]),
    "hypholoma fasciculare": _r(3, "Ложноопёнок серно-жёлтый ядовит: он вызывает тяжёлое "
                                   "расстройство желудка, описаны и смертельные случаи."),
    "omphalotus": _r(3, "Омфалоты ядовиты: через полчаса-два после еды начинаются сильная "
                        "рвота и понос."),
    "caloboletus radicans": _r(0, "Несъедобен из-за сильной горечи."),
    "mycosarcoma maydis": _r(1, "Молодые вздутия кукурузной головни едят в Мексике, там это "
                                "блюдо называют уитлакоче.", edible=["молодые вздутия"]),
    "ustilago maydis": _r(1, "Молодые вздутия кукурузной головни едят в Мексике, там это "
                             "блюдо называют уитлакоче.", edible=["молодые вздутия"]),
}

# Смертельные роды растений: если у гриба такая латынь, карточке досталась чужая
# латынь («Головач удлинённый» с Aconitum). Такую карточку правит человек.
_PLANT_GENERA = re.compile(r"^(aconitum|cicuta|conium|atropa|hyoscyamus|datura|veratrum|"
                           r"digitalis|colchicum|taxus|nerium|ricinus|oenanthe)\b", re.I)
_DEADLY_NAME = re.compile(r"^(строч|мухомор|бледная поганка|галерин|паутинник|лепиот)", re.I)
_EDIBLE_NAME = re.compile(r"^(сморч|опён|опен|шампиньон|сыроеж|подосинов|рыжик|груздь|белый)",
                          re.I)


def _keys(latin: str | None) -> tuple[str, str]:
    words = re.findall(r"[a-z-]+", (latin or "").lower())
    genus = words[0] if words else ""
    species = f"{genus} {words[1]}" if len(words) > 1 and words[1] not in ("l", "sp", "spp") else ""
    return species, genus


def identity_conflict(name: str | None, latin: str | None) -> str | None:
    """Русское имя и латынь говорят о разных грибах. Правила к такой карточке не
    применяются, её надо поправить руками."""
    species, genus = _keys(latin)
    n = (name or "").strip().lower()
    if _PLANT_GENERA.match(genus):
        return "латынь ядовитого растения у гриба"
    if _DEADLY_NAME.match(n) and genus in ("morchella", "agaricus", "russula", "armillaria"):
        return "имя ядовитого гриба, латынь съедобного"
    if _EDIBLE_NAME.match(n) and genus in ("gyromitra", "amanita", "galerina"):
        return "имя съедобного гриба, латынь ядовитого"
    expect = _NAME_BY_GENUS.get(genus)
    if expect and re.search(r"[а-яё]", n) and not re.match(expect, n):
        return "русское имя не из этого рода"
    return None


# Русские имена, которые ждём у рода из справочника. Латынь в атласе местами чужая
# («Майский гриб» с Agaricus, «Пестрец колокольчатый» с Galerina), и правило рода
# досталось бы не тому грибу.
_NAME_BY_GENUS = {
    "agaricus": r"шампиньон|агарик|печериц",
    "russula": r"сыроежк|подгруздок|валу",
    "tricholoma": r"рядовк|зеленушк",
    "armillaria": r"оп[её]н|опят",
    "kuehneromyces": r"оп[её]н|опят",
    "hypholoma": r"л[ож]*ноопен|л[ож]*ноопён|ложный|серно-ж|оп[её]н|опят",
    "marasmius": r"оп[её]н|негниючник",
    "calocybe": r"рядовк|майск|калоцибе",
    "entoloma": r"энтолом",
    "morchella": r"сморч",
    "gyromitra": r"строч",
    "macrolepiota": r"зонтик|гриб-зонтик",
    "chlorophyllum": r"зонтик|гриб-зонтик",
    "amanita": r"мухомор|поплав|кесарев|поганк",
    "cortinarius": r"паутинник|колпак|розитес",
    "lycoperdon": r"дождевик|головач|порховк|пороховк",
    "bovista": r"дождевик|головач|порховк|пороховк",
    "calvatia": r"дождевик|головач|лангерманн",
    "pleurotus": r"в[её]шенк|в[её]шонк",
    "galerina": r"галерин",
    "lepiota": r"лепиот",
    "scleroderma": r"ложнодождевик|склеродерм",
    "tylopilus": r"желчн|горчак",
    "rubroboletus": r"сатанинск|боровик",
    "omphalotus": r"омфалот",
    "clitocybe": r"говорушк",
    "inocybe": r"волоконниц",
}


def level_rule(name: str | None, latin: str | None) -> dict | None:
    """Уровень гриба, который задан правилом, или None, если решает модель."""
    if identity_conflict(name, latin):
        return None
    species, genus = _keys(latin)
    rule = LEVEL_RULES.get(species) or LEVEL_RULES.get(genus)
    if rule is None and genus == "amanita" and (name or "").lower().startswith("поплав"):
        rule = _r(2, _POPLAVOK, edible=["плодовое тело после варки"])
    if rule is None and species and genus in _MIXED_GENUS:
        rule = _r(0, _MIXED_GENUS[genus])
    if rule is None:
        return None
    # deadly_twin ставит не уровень, а справочник пар (fix_fungi_cards).
    return {**rule, "label": LABELS[rule["level"]], "deadly_twin": None}


def lookalikes(name: str | None, latin: str | None, rank: str | None = None) -> tuple[str | None, str | None]:
    """(deadly_twin, confused_with) из справочника; пусто, если пары нет."""
    if identity_conflict(name, latin):
        return None, None
    species, genus = _keys(latin)
    if species in _SHAMPIGNON_POISON:
        return None, CONFUSED_WITH[species]
    twin = DEADLY_TWIN.get(species)
    if twin is None and genus in DEADLY_TWIN:
        twin = DEADLY_TWIN[genus]
    if twin is None and not species and genus in _GENUS_TWIN_BY_NAME:
        prefix, value = _GENUS_TWIN_BY_NAME[genus]
        if (name or "").lower().startswith(prefix):
            twin = value
    confused = CONFUSED_WITH.get(species) or CONFUSED_WITH.get(genus)
    return twin, confused


# Фразы очерка, где модель пересказала прежнего двойника: «Опасный двойник —
# бледная поганка, не перепутай».
_TWIN_SENTENCE = re.compile(r"двойник|не перепута", re.I)
# Хвост фразы с двойником: «Условно съедобен; опасный двойник — X, не перепутай.»
_TWIN_TAIL = re.compile(r"\s*[;,—–-]?\s*(?:[а-яё]+\s+){0,2}двойник\b.*$", re.I)
_SAYS_DEADLY = re.compile(r"смертельн|есть нельзя|употреблять нельзя|ядовит|несъедоб|"
                          r"съедобность исключена", re.I)
_SAYS_SAFE = re.compile(r"съедоб|безопас", re.I)
_SAYS_LETHAL = re.compile(r"смертельн", re.I)
_SAYS_NO_DATA = re.compile(r"недостаточно данных|нет данных|достоверных данных|нет надёжных", re.I)


def _sentences(t: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if s]


def fix_text(t: str | None, level: int, level_changed: bool) -> str:
    """Убирает пересказ двойника и, если уровень сменился, фразы против нового уровня."""
    keep = []
    for s in _sentences(t or ""):
        if _TWIN_SENTENCE.search(s):
            head = _TWIN_TAIL.sub("", s).rstrip(" ,;:—–-")
            if len(head) < 15 or _TWIN_SENTENCE.search(head):
                continue
            s = head + "."
        if level_changed and level <= 2 and _SAYS_DEADLY.search(s):
            continue
        if level_changed and level >= 3 and _SAYS_SAFE.search(s):
            continue
        if level_changed and level == 3 and _SAYS_LETHAL.search(s):
            continue
        if level_changed and level > 0 and _SAYS_NO_DATA.search(s):
            continue
        keep.append(s)
    return " ".join(keep).strip()


def safety_block(level, rationale, edible, dangerous, twin, confused) -> dict:
    return {"level": level, "label": LABELS.get(level, "нет данных"), "rationale": rationale,
            "edible_parts": edible or [], "dangerous_parts": dangerous or [],
            "deadly_twin": twin, "confused_with": confused}


def fix_monograph(mono: dict, safety: dict, curated: bool = False) -> dict:
    """``curated``: уровень и обоснование заданы правилом, а не моделью; тогда
    опустевший вердикт заменяется первой фразой обоснования."""
    m = dict(mono)
    old = m.get("safety") or {}
    level = safety["level"]
    changed = old.get("level") != level
    m["safety"] = safety
    m["is_toxic"] = level >= 3
    if "kingdom" in m:
        m["kingdom"] = "гриб"       # 95 грибов стояли растениями до 26.10.2026
    roles = [r for r in (m.get("roles") or []) if r != "toxic"]
    if level >= 3:
        roles.append("toxic")
    if "roles" in m or level >= 3:
        m["roles"] = roles
    verdict = fix_text(m.get("verdict"), level, changed)
    if not verdict and curated and safety.get("rationale"):
        parts = _sentences(safety["rationale"])
        verdict = " ".join(parts[:2]) if len(" ".join(parts[:2])) <= 260 else parts[0]
    if verdict:
        m["verdict"] = verdict
    else:
        m.pop("verdict", None)
    cautions = m.get("cautions")
    if isinstance(cautions, dict) and cautions.get("text"):
        t = fix_text(cautions["text"], level, changed)
        m["cautions"] = {**cautions, "text": t or None}
    return m


async def fix_fungi_cards(db: AsyncSession, apply: bool = False) -> dict:
    """Приводит грибы к правилам и справочнику. Возвращает отчёт и прежние значения."""
    rows = (await db.execute(text("""
        SELECT p.id, p.rank, p.name, p.name_latin, p.safety_level, p.safety_rationale, p.is_toxic,
               p.edible_parts, p.dangerous_parts, p.deadly_twin, p.confused_with, m.monograph
        FROM plants p LEFT JOIN plant_reader_monograph m ON m.plant_id = p.id
        WHERE p.kingdom = 'гриб' AND p.rank IN ('species', 'genus')
    """))).all()
    changed, backup, conflicts = [], [], []
    for r in rows:
        conflict = identity_conflict(r.name, r.name_latin)
        if conflict:
            conflicts.append({"id": str(r.id), "name": r.name, "name_latin": r.name_latin,
                              "level": r.safety_level, "why": conflict})
        rule = level_rule(r.name, r.name_latin)
        twin, confused = lookalikes(r.name, r.name_latin, r.rank)
        if rule:
            level, why = rule["level"], rule["rationale"]
            ep, dp = rule["edible_parts"] or None, rule["dangerous_parts"] or None
        else:
            level, why, ep, dp = r.safety_level, r.safety_rationale, r.edible_parts, r.dangerous_parts
        if (level or 0) >= 3:
            twin = None             # ядовитый двойник бывает только у съедобного гриба
        mono = r.monograph if isinstance(r.monograph, dict) else (json.loads(r.monograph) if r.monograph else None)
        new_mono = None
        if mono is not None and level is not None:
            new_mono = fix_monograph(mono, safety_block(level, why, ep, dp, twin, confused),
                                     curated=rule is not None)
        if (level == r.safety_level and why == r.safety_rationale and ep == r.edible_parts
                and dp == r.dangerous_parts and twin == r.deadly_twin and confused == r.confused_with
                and new_mono == mono):
            continue
        backup.append({"id": str(r.id), "safety_level": r.safety_level,
                       "safety_rationale": r.safety_rationale, "is_toxic": r.is_toxic,
                       "edible_parts": r.edible_parts, "dangerous_parts": r.dangerous_parts,
                       "deadly_twin": r.deadly_twin, "confused_with": r.confused_with,
                       "monograph": mono})
        changed.append({"id": str(r.id), "name": r.name, "name_latin": r.name_latin,
                        "level_from": r.safety_level, "level_to": level,
                        "twin_from": r.deadly_twin, "twin_to": twin, "confused_with": confused,
                        "verdict_from": (mono or {}).get("verdict"),
                        "verdict_to": (new_mono or {}).get("verdict")})
        if apply:
            await db.execute(text("""
                UPDATE plants SET safety_level = :l, safety_rationale = :why, is_toxic = :tox,
                       edible_parts = :ep, dangerous_parts = :dp, deadly_twin = :tw,
                       confused_with = :cw
                WHERE id = :id"""), {"id": r.id, "l": level, "why": why,
                                     "tox": (level or 0) >= 3, "ep": ep, "dp": dp,
                                     "tw": twin, "cw": confused})
            if new_mono is not None:
                await db.execute(text(
                    "UPDATE plant_reader_monograph SET monograph = CAST(:m AS jsonb), updated_at = now() "
                    "WHERE plant_id = :id"),
                    {"id": r.id, "m": json.dumps(new_mono, ensure_ascii=False)})
    if apply:
        await db.commit()
    return {"changed": changed, "backup": backup, "conflicts": conflicts}
