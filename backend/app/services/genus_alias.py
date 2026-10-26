"""Роды, которые определитель называет по-новому, а атлас по-старому (или наоборот).

Замер отказов 26.10.2026: за 30 дней 59 снимков из 347 остались без карточки
только потому, что род переименован. Pl@ntNet называет калатею Goeppertia, а
карточка заведена как Calathea; сентябринку — Symphyotrichum, а карточка лежит
под Aster novi-belgii. Вид тот же, эпитет тот же, разошлось только имя рода.

Здесь только перекомбинации, где растение одно и то же. Сегрегаты, которые в
атласе означают другое растение, сюда не идут: Curio (жемчужная нить) нельзя
сводить к Senecio, под этим родом у нас крестовник.

Словарь для определения по фото. У ``genus._GENUS_SYN`` другая задача (сборка
родового яруса гербария), и часть его склеек для снимка была бы враньём."""

ENGINE_TO_ATLAS = {
    "goeppertia": "calathea",        # калатеи, выделены из Calathea в 2012
    "saintpaulia": "streptocarpus",  # сенполия, сведена в Streptocarpus в 2015
    "symphyotrichum": "aster",       # сентябринки
    "dendranthema": "chrysanthemum",
    "hylotelephium": "sedum",        # очиток видный
    "phedimus": "sedum",
    "aristaloe": "aloe",
    "gonialoe": "aloe",
    "haworthiopsis": "haworthia",
    "rhipsalidopsis": "schlumbergera",
    "apocissus": "cissus",
    "heptapleurum": "schefflera",
    "oreomecon": "papaver",          # мак голостебельный
    "urceolina": "eucharis",
    "eriocapitella": "anemone",      # японский анемон
    "scandosorbus": "sorbus",
    "lophiolepis": "cirsium",
    "eutrochium": "eupatorium",
    "bryophyllum": "kalanchoe",
    "euphrosyne": "cyclachaena",     # циклахена дурнишниколистная
}


def atlas_alias(latin: str | None) -> str | None:
    """То же имя с родом, под которым растение заведено в атласе, или None,
    если род не переименован. «Goeppertia insignis» → «Calathea insignis»."""
    parts = (latin or "").split()
    if not parts:
        return None
    target = ENGINE_TO_ATLAS.get(parts[0].lower())
    if not target:
        return None
    return " ".join([target.capitalize(), *parts[1:]])
