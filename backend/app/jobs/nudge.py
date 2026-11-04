"""Abandoned-order nudger for the qtickets master-class sales.

Polls the Qtickets REST API for UNPAID orders that were started recently, are
past a short grace period, and are for a FUTURE show — then reports each once to
the owner in Telegram with the client's contact + the ready «доплатить»
(payment_url) link, so he can nudge them. Reporting only; no automated emails to
clients (that is a deliberate later step).

Runs as its own container (reuses the backend image). Dedup via `qtickets_nudges`.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import text as sql

from app.database import async_session, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nastoiki.nudge")

QT = "https://qtickets.ru/api/rest/v1"
QT_TOKEN = os.environ.get("QTICKETS_API_TOKEN", "")
BOT_TOKEN = os.environ.get("NASTOIKI_BOT_TOKEN", "")
OWNER_TG_ID = os.environ.get("OWNER_TG_ID", "4683166")  # Oleg (@hippo26)

POLL_SEC = int(os.environ.get("NUDGE_POLL_SEC", "3600"))       # hourly
MAX_AGE_DAYS = int(os.environ.get("NUDGE_MAX_AGE_DAYS", "4"))   # ignore older abandoned carts
GRACE_MIN = int(os.environ.get("NUDGE_GRACE_MIN", "30"))        # let them finish paying first

_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]


async def ensure_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(sql("""
            CREATE TABLE IF NOT EXISTS qtickets_nudges (
              order_id    BIGINT PRIMARY KEY,
              notified_at TIMESTAMPTZ DEFAULT now()
            )"""))


async def already_notified(order_id) -> bool:
    async with async_session() as s:
        row = (await s.execute(sql(
            "INSERT INTO qtickets_nudges (order_id) VALUES (:id) "
            "ON CONFLICT (order_id) DO NOTHING RETURNING order_id"),
            {"id": order_id})).first()
        await s.commit()
        return row is None  # None inserted → already there → already notified


async def qt_get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    r = await client.get(f"{QT}{path}", params=params or {},
                         headers={"Authorization": f"Bearer {QT_TOKEN}"})
    try:
        return r.json()
    except ValueError:
        return {}


async def fetch_all_orders(client) -> list[dict]:
    """Orders come OLDEST-first, perPage max 100, no page meta — walk to the end."""
    out: list[dict] = []
    for page in range(1, 61):  # safety cap ~6000 orders
        data = (await qt_get(client, "/orders", {"perPage": 100, "page": page})).get("data", [])
        if not data:
            break
        out += data
        if len(data) < 100:
            break
    return out


async def show_maps(client):
    """event_id → name ; show_id → (event_name, start_date_iso)."""
    evs = (await qt_get(client, "/events")).get("data", [])
    en, sm = {}, {}
    for e in evs:
        en[e.get("id")] = e.get("name")
        for s in (e.get("shows") or []):
            sm[s.get("id")] = (e.get("name"), s.get("start_date"))
    return en, sm


def _parse(iso: str):
    try:
        return datetime.fromisoformat(iso)
    except Exception:  # noqa: BLE001
        return None


def _ago(dt: datetime) -> str:
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    h = int(secs // 3600)
    if h < 1:
        return f"{int(secs // 60)} мин"
    if h < 24:
        return f"{h} ч"
    return f"{h // 24} дн"


def _date_ru(iso: str) -> str:
    d = _parse(iso)
    return f"{d.day} {_MONTHS[d.month]} {d.year}" if d else "дата уточняется"


async def send_owner(text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                     json={"chat_id": OWNER_TG_ID, "text": text, "disable_web_page_preview": True})


async def run_once() -> None:
    now = datetime.now(timezone.utc).timestamp()
    async with httpx.AsyncClient(timeout=60) as client:
        orders = await fetch_all_orders(client)
        en, sm = await show_maps(client)
    log.info(f"fetched {len(orders)} orders")

    sent = 0
    for o in orders:
        if o.get("payed"):
            continue
        ct = _parse(o.get("created_at") or "")
        if not ct:
            continue
        age = now - ct.timestamp()
        if age > MAX_AGE_DAYS * 86400 or age < GRACE_MIN * 60:
            continue  # too old (dead) or too fresh (still paying)

        basket = (o.get("baskets") or [{}])[0]
        ename, sdate = sm.get(basket.get("show_id"), (en.get(o.get("event_id")), None))
        # skip orders for a show that already happened
        sd = _parse(sdate) if sdate else None
        if sd and sd.timestamp() < now:
            continue

        if await already_notified(o["id"]):
            continue

        client_ = o.get("client") or {}
        details = client_.get("details") or {}
        contact = client_.get("email") or "—"
        phone = details.get("phone")
        report = (
            "🔔 Незавершённый заказ на мастер-класс\n\n"
            f"📅 {ename or 'мастер-класс'}"
            + (f" — {_date_ru(sdate)}" if sdate else "") + "\n"
            f"💰 {o.get('price')} ₽\n"
            f"👤 {contact}" + (f", {phone}" if phone else "") + "\n"
            f"🕐 начат {_ago(ct)} назад\n\n"
            "Ссылка «доплатить» (можно отправить клиенту):\n"
            f"{o.get('payment_url')}"
        )
        await send_owner(report)
        sent += 1
        log.info(f"nudged order {o['id']} ({contact})")

    if sent:
        log.info(f"reported {sent} abandoned order(s)")


async def main() -> None:
    if not QT_TOKEN or not BOT_TOKEN:
        log.error("QTICKETS_API_TOKEN / NASTOIKI_BOT_TOKEN not set — nudger idle")
        while True:
            await asyncio.sleep(3600)
    await ensure_table()
    log.info(f"nudger online: poll={POLL_SEC}s grace={GRACE_MIN}m max_age={MAX_AGE_DAYS}d owner={OWNER_TG_ID}")
    while True:
        try:
            await run_once()
        except Exception as e:  # noqa: BLE001
            log.error(f"nudge run failed: {e}")
        await asyncio.sleep(POLL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
