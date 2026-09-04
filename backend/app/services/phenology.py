"""Сезон вида: показывать ли его в квесте этого месяца.

Откуда взялось. 4 сентября 2026 квест предложил искать медуницу — она отцвела
в мае. Сезонный фильтр стоял только на именованных местах и отбирал виды по
месяцу, когда их ФОТОГРАФИРУЮТ, а не когда их можно узнать: листья медуницы
снимают до осени, и она проходила. Кастомные и личные квесты сезона не знали
вовсе — там «весь сезон» записан в коде намеренно, чтобы у дачи не выходил
пустой набор.

Два свидетеля, в таком порядке:

* iNat, доля наблюдений по месяцам — главный. Он и есть «когда люди находят и
  узнают». У медуницы неясной 40% в апреле, 26% в мае, 2% в сентябре.
* корпус, месяцы сбора надземных частей — только когда iNat вида не знает.
  Один он слишком щедр: «летом», «осенью», «в течение всего лета» дают той же
  медунице март–август. Перебивать им iNat нельзя.

Нет данных ни там, ни там — вид проходит: молчание не повод прятать.

Фильтр стоит на ЧТЕНИИ (place_set), а не на сборке наборов. Пул значка
кумулятивный по всем окнам (решение Олега 24 августа) и не трогается; окно —
подсказка «что искать сейчас», и именно её мы делаем честной. Заодно это чинит
личные места, застрявшие в августовском окне: они фильтруются текущим месяцем
при каждом показе.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Доля годовых наблюдений, ниже которой вид в этом месяце «не сезон».
# Порог выбран по замеру сентябрьских наборов (см. коммит): медуница 2% —
# мимо; крапива, которую снимают весь год, — в любом месяце выше.
IN_SEASON_SHARE = 0.05

# Гистограмма ячейки региона считается надёжной от стольких наблюдений; меньше —
# берём мировую. Ниже этого доли по месяцам — шум из десятка снимков.
CELL_MIN_OBS = 30


def cell_of(lat: float, lng: float) -> tuple[int, int]:
    """Ячейка 2°×2° по юго-западному углу — та же, что в миграции 028 и в замерах."""
    import math
    return int(math.floor(lat / 2) * 2), int(math.floor(lng / 2) * 2)


def in_season(inat_months: list[float] | None, corpus_months: list[int] | None,
              month: int, cell_months: list[float] | None = None,
              cell_n_obs: int | None = None) -> bool | None:
    """True/False по данным; None — данных нет (вызывающий решает, обычно пропускает).

    Порядок свидетелей: ячейка региона (если наблюдений в ней хватает) → мировая
    гистограмма → корпус. Ячейка первой, потому что климат — местный: Сахалин и
    Сочи на одной широте, и только наблюдения вокруг самого места это знают."""
    if (cell_months and len(cell_months) == 12 and sum(cell_months) > 0
            and (cell_n_obs or 0) >= CELL_MIN_OBS):
        return cell_months[month - 1] >= IN_SEASON_SHARE
    if inat_months and len(inat_months) == 12 and sum(inat_months) > 0:
        return inat_months[month - 1] >= IN_SEASON_SHARE
    if corpus_months:
        return month in corpus_months
    return None


async def phenology_map(db: AsyncSession, latin_keys: list[str],
                        cell: tuple[int, int] | None = None) -> dict[str, dict]:
    """{latin_key: {inat_months, corpus_months, cell_months, cell_n_obs}} для ключей.
    `cell` — ячейка региона (см. cell_of); без неё поля ячейки пусты."""
    if not latin_keys:
        return {}
    keys = list(set(latin_keys))
    if cell is None:
        rows = (await db.execute(text(
            "SELECT latin_key, inat_months, corpus_months, NULL AS cell_months, NULL AS cell_n_obs "
            "FROM species_phenology WHERE latin_key = ANY(:k)"), {"k": keys})).all()
    else:
        rows = (await db.execute(text(
            "SELECT g.latin_key, g.inat_months, g.corpus_months, c.inat_months AS cell_months, "
            "c.inat_n_obs AS cell_n_obs "
            "FROM species_phenology g LEFT JOIN species_phenology_cell c "
            "  ON c.latin_key = g.latin_key AND c.cell_lat = :clat AND c.cell_lng = :clng "
            "WHERE g.latin_key = ANY(:k)"),
            {"k": keys, "clat": cell[0], "clng": cell[1]})).all()
    return {r.latin_key: {"inat_months": r.inat_months, "corpus_months": r.corpus_months,
                          "cell_months": r.cell_months, "cell_n_obs": r.cell_n_obs}
            for r in rows}


async def split_by_season(db: AsyncSession, items: list[dict], month: int,
                          key_field: str = "latin_key",
                          cell: tuple[int, int] | None = None) -> tuple[list[dict], list[dict]]:
    """Разделить карточки на «сейчас» и «не сезон». Без данных — в «сейчас».
    `cell` — ячейка региона места/съёмки: с ней сезон считается по местным наблюдениям."""
    ph = await phenology_map(db, [i[key_field] for i in items if i.get(key_field)], cell)
    now, later = [], []
    for it in items:
        p = ph.get(it.get(key_field))
        verdict = (in_season(p["inat_months"], p["corpus_months"], month,
                             p.get("cell_months"), p.get("cell_n_obs")) if p else None)
        (later if verdict is False else now).append(it)
    return now, later
