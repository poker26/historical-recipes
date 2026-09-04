# -*- coding: utf-8 -*-
"""Пуш «сезон открыт» — по факту пересечения порога, а не по календарю.

RFC всесезонной версии §4.7 (Phase A). Механизм возврата после зимы: по каждому
виду и ЯЧЕЙКЕ региона известно, в какой месяц доля наблюдений переваливает
порог сезона. Когда это случается в ячейке, где человек снимал, — ему уходит
одно письмо: «Сезон открыт: медуница — уже находят рядом». Одно на устройство в
месяц; журнал push_campaign_log физически не даёт отправить дважды.

Почему по ячейке, а не по миру: по мировой гистограмме март в Москве выглядит
живым (73 места из 95), по местным наблюдениям живым становится апрель. Пуш по
миру ушёл бы на месяц раньше, чем что-то распустится, — и это был бы обман.

Запуск: `python -m app.jobs.season_openers [--apply] [--month N] [--limit N]`.
Без --apply — только список, кому и что ушло бы.
"""
import argparse
import asyncio
import logging
from datetime import date

from sqlalchemy import text

from app.database import async_session
from app.services.campaigns import within_polite_hours
from app.services.phenology import IN_SEASON_SHARE, CELL_MIN_OBS, cell_of
from app.services.push import send_to_device

logger = logging.getLogger("jobs.season_openers")


async def openers_for_cell(db, cell: tuple[int, int], month: int, limit: int = 3) -> list[dict]:
    """Виды, у которых в этой ячейке сезон ОТКРЫЛСЯ в месяце `month`: доля ≥ порога
    сейчас и < порога месяцем раньше; с карточкой и монографом; по убыванию
    наблюдений — чтобы звать искать то, что реально находят."""
    prev = 12 if month == 1 else month - 1
    rows = (await db.execute(text("""
        SELECT c.latin_key, c.inat_n_obs, p.id::text AS plant_id, p.name
        FROM species_phenology_cell c
        JOIN plants p ON lower(split_part(p.name_latin,' ',1)||' '||split_part(p.name_latin,' ',2)) = c.latin_key
        JOIN plant_reader_monograph m ON m.plant_id = p.id AND m.reviewed
        WHERE c.cell_lat = :clat AND c.cell_lng = :clng
          AND c.inat_months IS NOT NULL AND c.inat_n_obs >= :minobs
          AND c.inat_months[:m] >= :share AND c.inat_months[:pm] < :share
        ORDER BY c.inat_n_obs DESC LIMIT :lim"""),
        {"clat": cell[0], "clng": cell[1], "m": month, "pm": prev,
         "share": IN_SEASON_SHARE, "minobs": CELL_MIN_OBS, "lim": limit})).all()
    return [{"latin_key": r.latin_key, "plant_id": r.plant_id, "name": r.name, "n_obs": r.inat_n_obs}
            for r in rows]


async def targets(db, campaign: str) -> list[dict]:
    """Кому: есть токен, не заблокирован, снимал с гео (значит, известна ячейка), и
    эту кампанию ещё не получал. Ячейка — по последнему снимку с координатами."""
    rows = (await db.execute(text("""
        SELECT d.device_key::text AS dk, i.lat, i.lng
        FROM quest_devices d
        JOIN LATERAL (SELECT lat, lng FROM identifications
                       WHERE device_key = d.device_key AND lat IS NOT NULL AND lng IS NOT NULL
                       ORDER BY created_at DESC LIMIT 1) i ON true
        WHERE COALESCE(d.blocked, false) = false
          AND EXISTS (SELECT 1 FROM push_tokens p WHERE p.device_key = d.device_key)
          AND NOT EXISTS (SELECT 1 FROM push_campaign_log l
                           WHERE l.campaign = :c AND l.device_key = d.device_key)"""),
        {"c": campaign})).all()
    return [{"device_key": r.dk, "cell": cell_of(r.lat, r.lng)} for r in rows]


async def run(month: int | None = None, apply: bool = False, limit: int = 200) -> dict:
    today = date.today()
    month = month or today.month
    campaign = f"opener:{today.year}-{month:02d}"
    out = {"campaign": campaign, "status": "sent" if apply else "dry_run",
           "candidates": 0, "with_opener": 0, "sent": 0, "skipped": 0, "sample": []}
    if apply and not within_polite_hours():
        out["status"] = "outside_polite_hours"
        return out
    async with async_session() as db:
        tg = (await targets(db, campaign))[:limit]
        out["candidates"] = len(tg)
        cache: dict[tuple[int, int], list[dict]] = {}
        for t in tg:
            if t["cell"] not in cache:
                cache[t["cell"]] = await openers_for_cell(db, t["cell"], month)
            ops = cache[t["cell"]]
            if not ops:
                out["skipped"] += 1
                continue
            out["with_opener"] += 1
            top = ops[0]
            title = f"Сезон открыт: {top['name']}"
            body = "Уже находят рядом с вами — самое время выйти с камерой."
            if len(out["sample"]) < 8:
                out["sample"].append({"device": t["device_key"][:8], "cell": t["cell"], "plant": top["name"]})
            if not apply:
                continue
            res = await send_to_device(db, t["device_key"], title, body,
                                       data={"campaign": campaign, "screen": "plant", "plant_id": top["plant_id"]})
            ok = res.get("sent", 0) > 0
            await db.execute(text("""
                INSERT INTO push_campaign_log (campaign, device_key, outcome, detail)
                VALUES (:c, CAST(:dk AS uuid), :o, :d)
                ON CONFLICT (campaign, device_key) DO NOTHING"""),
                {"c": campaign, "dk": t["device_key"], "o": "sent" if ok else "failed",
                 "d": f"{top['latin_key']} ({top['n_obs']} набл.)"})
            await db.commit()
            out["sent"] += 1 if ok else 0
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--month", type=int)
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run(a.month, a.apply, a.limit)))


if __name__ == "__main__":
    main()
