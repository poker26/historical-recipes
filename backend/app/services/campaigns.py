"""Разовые письма молчащим устройствам — с правилами вежливости внутри механизма.

Замер 2026-09-02: устройств 227, из них 112 не сделали ни одного снимка, и лишь у
18 есть пуш-токен — токен появляется при первом запуске, поэтому до тех, кто
приложение не открывал, пушем не дотянуться вовсе. А доля молчунов среди новых
устройств прыгнула с 30% до 66% ровно на неделе выхода iOS-версии 2.0.1, которую
убивал сторожевой таймер, — то есть это чаще «не смог», чем «не заинтересовался».
Отсюда и тон письма: не зазывать, а признать поломку и позвать вернуться.

Что зашито в механизм, а не оставлено на совесть отправляющего:
  * одно письмо на устройство за всё время — первичный ключ (campaign, device_key)
    физически не даст отправить дважды;
  * тихие часы: не пишем ночью. Часового пояса у молчунов нет (гео они не давали),
    поэтому ориентир — Москва и середина дня;
  * проверка молчания в САМУЮ минуту отправки: успел сфотографировать — письмо
    отменяется, звать уже некуда;
  * сухой прогон по умолчанию: чтобы отправить по-настоящему, надо сказать явно.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import push

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
QUIET_FROM_H, QUIET_TO_H = 21, 10      # с 21:00 до 10:00 МСК не пишем
MIN_AGE_DAYS = 3                       # свежепоставившим дать спокойно дойти самим


def within_polite_hours(now: datetime | None = None) -> bool:
    hour = (now or datetime.now(MSK)).astimezone(MSK).hour
    return QUIET_TO_H <= hour < QUIET_FROM_H


async def silent_devices(db: AsyncSession, campaign: str,
                         min_age_days: int = MIN_AGE_DAYS) -> list[dict]:
    """Кому есть смысл писать: токен есть, снимков ноль, не заблокирован, стоит
    дольше `min_age_days`, и этой кампании ещё не получал."""
    rows = (await db.execute(text("""
        SELECT d.device_key::text AS dk,
               d.created_at::date AS registered,
               (now()::date - d.created_at::date) AS days,
               (SELECT string_agg(DISTINCT p.platform, '+') FROM push_tokens p
                 WHERE p.device_key = d.device_key) AS platforms
        FROM quest_devices d
        WHERE COALESCE(d.blocked, false) = false
          AND d.created_at < now() - (:age || ' days')::interval
          AND EXISTS (SELECT 1 FROM push_tokens p WHERE p.device_key = d.device_key)
          AND NOT EXISTS (SELECT 1 FROM identifications i WHERE i.device_key = d.device_key)
          AND NOT EXISTS (SELECT 1 FROM push_campaign_log l
                           WHERE l.campaign = :c AND l.device_key = d.device_key)
        ORDER BY d.created_at"""), {"age": str(min_age_days), "c": campaign})).all()
    return [{"device_key": r.dk, "registered": str(r.registered),
             "days": r.days, "platforms": r.platforms} for r in rows]


async def _still_silent(db: AsyncSession, device_key: str) -> bool:
    n = (await db.execute(text(
        "SELECT count(*) FROM identifications WHERE device_key = CAST(:dk AS uuid)"),
        {"dk": device_key})).scalar() or 0
    return n == 0


async def run_campaign(db: AsyncSession, campaign: str, title: str, body: str,
                       *, apply: bool = False, limit: int = 100,
                       ignore_quiet_hours: bool = False) -> dict:
    """Отправить кампанию молчунам. По умолчанию — сухой прогон."""
    if not (ignore_quiet_hours or within_polite_hours()):
        return {"status": "quiet_hours", "now_msk": datetime.now(MSK).strftime("%H:%M"),
                "window": f"{QUIET_TO_H}:00–{QUIET_FROM_H}:00 МСК"}
    targets = (await silent_devices(db, campaign))[:limit]
    out = {"status": "dry_run" if not apply else "sent", "campaign": campaign,
           "targets": len(targets), "sent": 0, "skipped": 0, "failed": 0,
           "title": title, "body": body, "sample": targets[:5]}
    if not apply:
        return out
    for t in targets:
        dk = t["device_key"]
        if not await _still_silent(db, dk):        # успел снять, пока мы собирались
            await _log(db, campaign, dk, "skipped", "успел сделать снимок")
            out["skipped"] += 1
            continue
        res = await push.send_to_device(db, dk, title, body,
                                        data={"campaign": campaign, "screen": "identify"})
        ok = (res.get("sent") or 0) > 0
        await _log(db, campaign, dk, "sent" if ok else "failed", str(res)[:200])
        out["sent" if ok else "failed"] += 1
    await db.commit()
    return out


async def _log(db: AsyncSession, campaign: str, device_key: str,
               outcome: str, detail: str) -> None:
    await db.execute(text("""
        INSERT INTO push_campaign_log (campaign, device_key, outcome, detail)
        VALUES (:c, CAST(:dk AS uuid), :o, :d)
        ON CONFLICT (campaign, device_key) DO NOTHING"""),
        {"c": campaign, "dk": device_key, "o": outcome, "d": detail})
