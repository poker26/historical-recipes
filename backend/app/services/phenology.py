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


def in_season(inat_months: list[float] | None, corpus_months: list[int] | None,
              month: int) -> bool | None:
    """True/False по данным; None — данных нет (вызывающий решает, обычно пропускает)."""
    if inat_months and len(inat_months) == 12 and sum(inat_months) > 0:
        return inat_months[month - 1] >= IN_SEASON_SHARE
    if corpus_months:
        return month in corpus_months
    return None


async def phenology_map(db: AsyncSession, latin_keys: list[str]) -> dict[str, dict]:
    """{latin_key: {inat_months, corpus_months}} для запрошенных ключей."""
    if not latin_keys:
        return {}
    rows = (await db.execute(text(
        "SELECT latin_key, inat_months, corpus_months FROM species_phenology "
        "WHERE latin_key = ANY(:k)"), {"k": list(set(latin_keys))})).all()
    return {r.latin_key: {"inat_months": r.inat_months, "corpus_months": r.corpus_months}
            for r in rows}


async def split_by_season(db: AsyncSession, items: list[dict], month: int,
                          key_field: str = "latin_key") -> tuple[list[dict], list[dict]]:
    """Разделить карточки на «сейчас» и «не сезон». Без данных — в «сейчас»."""
    ph = await phenology_map(db, [i[key_field] for i in items if i.get(key_field)])
    now, later = [], []
    for it in items:
        p = ph.get(it.get(key_field))
        verdict = in_season(p["inat_months"], p["corpus_months"], month) if p else None
        (later if verdict is False else now).append(it)
    return now, later
