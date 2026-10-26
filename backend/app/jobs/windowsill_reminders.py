# -*- coding: utf-8 -*-
"""Напоминания «Моего подоконника»: при смене сезона и раз в две недели.

Решение владельца 26.10.2026: пуш при смене режима ухода (зима, весна, лето,
осень) и между ними раз в 14 дней «пора проверить землю». В день сезонного пуша
обычный не уходит. Пишем только тем, у кого на подоконнике есть растения, кто не
выключил напоминания и у кого есть токен пушей; только в вежливые часы.

Запуск раз в день из cron на проде:
  docker exec historical-recipes-backend-1 python -m app.jobs.windowsill_reminders --apply
Без --apply только список, кому и что ушло бы.
"""
import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from app.database import async_session
from app.services.campaigns import within_polite_hours
from app.services.push import send_to_device
from app.services.windowsill import CHECK_EVERY_DAYS, CHECK_PUSH, SEASON_PUSH, season_of

logger = logging.getLogger("jobs.windowsill_reminders")


def due(season: str, last_season: str | None, last_at: datetime | None, now: datetime) -> str | None:
    """Какой пуш положен: 'season', 'check' или None."""
    if last_season != season:
        return "season"
    if last_at is None or now - last_at >= timedelta(days=CHECK_EVERY_DAYS):
        return "check"
    return None


async def run(apply: bool, limit: int) -> dict:
    now = datetime.now(timezone.utc)
    season = season_of(date.today())
    out = {"season": season, "devices": 0, "season_sent": 0, "check_sent": 0, "failed": 0, "sample": []}
    if apply and not within_polite_hours():
        out["skipped"] = "не вежливое время"
        return out
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT w.device_key::text dk, count(*) n, p.last_season, p.last_reminder_at,
                   coalesce(p.reminders, true) reminders
            FROM windowsill_plants w
            LEFT JOIN windowsill_prefs p ON p.device_key = w.device_key
            WHERE EXISTS (SELECT 1 FROM push_tokens t WHERE t.device_key = w.device_key AND t.token <> '')
            GROUP BY 1, 3, 4, 5
            LIMIT :lim"""), {"lim": limit})).all()
        for r in rows:
            if not r.reminders:
                continue
            kind = due(season, r.last_season, r.last_reminder_at, now)
            if kind is None:
                continue
            out["devices"] += 1
            title, body = SEASON_PUSH[season] if kind == "season" else CHECK_PUSH
            if len(out["sample"]) < 8:
                out["sample"].append({"device": r.dk[:8], "kind": kind, "plants": r.n})
            if not apply:
                continue
            res = await send_to_device(db, r.dk, title, body,
                                       data={"campaign": f"windowsill-{kind}", "screen": "windowsill"})
            ok = res.get("sent", 0) > 0
            # Отметку ставим и при неудаче: иначе сломанный токен получал бы попытку каждый день.
            await db.execute(text("""
                INSERT INTO windowsill_prefs (device_key, last_reminder_at, last_season)
                VALUES (CAST(:dk AS uuid), :t, :s)
                ON CONFLICT (device_key) DO UPDATE
                SET last_reminder_at = EXCLUDED.last_reminder_at, last_season = EXCLUDED.last_season"""),
                {"dk": r.dk, "t": now, "s": season})
            await db.commit()
            if ok:
                out["season_sent" if kind == "season" else "check_sent"] += 1
            else:
                out["failed"] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=5000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(run(a.apply, a.limit)))


if __name__ == "__main__":
    main()
