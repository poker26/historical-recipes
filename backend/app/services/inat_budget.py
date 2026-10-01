"""Общий бюджет запросов к iNaturalist для всех процессов сервера (бэкенд и диспетчер).

Лимит у iNaturalist один на IP сервера. 29–30.09.2026 его съели обход сайта роботами и
фоновые шаги чистки карточек, и приложение не дождалось трети определений за день. Правила:

- отказ 429 в любом процессе ставит общую паузу (Retry-After или 60 с) в таблице
  inat_rate_state; все процессы её видят и в iNaturalist не ходят, пока она не кончится;
- фоновые задачи после отказа ждут дольше (5 минут), а в обычное время берут жетоны из
  общего ведра на 20 запросов в минуту на весь сервер; так запросам пользователей всегда
  остаётся запас;
- запросы пользователей (определение, «Растения рядом», страницы сайта) жетонов не берут:
  их защищают короткие сроки и кэши, а общую паузу они соблюдают;
- каждый отказ пишется в inat_rate_events, по ним Grafana поднимает тревогу.

Состояние читается из базы не чаще раза в 5 секунд на процесс. Если база недоступна,
работаем по последнему известному состоянию: бюджет не должен ронять сами запросы.
"""

import asyncio
import logging
import time

from sqlalchemy import text

from app.database import async_session

logger = logging.getLogger(__name__)

BG_RATE_PER_S = 20 / 60      # фоновых запросов в секунду на весь сервер
BG_BURST = 5.0               # сколько фоновых запросов можно сделать подряд
SHARED_PAUSE_S = 60.0        # пауза для всех после отказа, если iNaturalist не назвал срок
BG_PAUSE_S = 300.0           # пауза для фоновых задач после отказа
_CHECK_EVERY_S = 5.0

_state = {"checked": 0.0, "shared_until": 0.0, "bg_until": 0.0}


async def _refresh(force: bool = False) -> None:
    now = time.time()
    if not force and now - _state["checked"] < _CHECK_EVERY_S:
        return
    _state["checked"] = now
    try:
        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT name, extract(epoch FROM paused_until) FROM inat_rate_state"))).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("inat budget: state read failed: %s", type(e).__name__)
        return
    for name, until in rows:
        if name == "shared":
            _state["shared_until"] = max(_state["shared_until"], float(until or 0))
        elif name == "background":
            _state["bg_until"] = max(_state["bg_until"], float(until or 0))


async def paused(background: bool = False) -> bool:
    """Нельзя ходить в iNaturalist сейчас: общая пауза (и, для фона, фоновая)."""
    await _refresh()
    now = time.time()
    return now < _state["shared_until"] or (background and now < _state["bg_until"])


def _retry_after_s(value: str | None) -> float:
    return float(value) if (value or "").isdigit() else SHARED_PAUSE_S


async def note_429(retry_after: str | None, source: str) -> None:
    """Отказ 429: пауза всем процессам и запись в журнал отказов."""
    secs = _retry_after_s(retry_after)
    now = time.time()
    _state["shared_until"] = max(_state["shared_until"], now + secs)
    _state["bg_until"] = max(_state["bg_until"], now + BG_PAUSE_S)
    logger.warning("iNat 429 (%s): all processes pause %.0fs, background %.0fs", source, secs, BG_PAUSE_S)
    try:
        async with async_session() as db:
            await db.execute(text("""
                UPDATE inat_rate_state SET paused_until = GREATEST(COALESCE(paused_until, now()),
                       now() + make_interval(secs => CAST(:s AS double precision)))
                WHERE name = 'shared'"""), {"s": secs})
            await db.execute(text("""
                UPDATE inat_rate_state SET paused_until = GREATEST(COALESCE(paused_until, now()),
                       now() + make_interval(secs => CAST(:s AS double precision)))
                WHERE name = 'background'"""), {"s": BG_PAUSE_S})
            await db.execute(text(
                "INSERT INTO inat_rate_events (source, status, retry_after) VALUES (:src, 429, :ra)"),
                {"src": source[:60], "ra": int(secs)})
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("inat budget: state write failed: %s", type(e).__name__)


def _heartbeat() -> None:
    """Фоновая задача ждёт жетон долго: Temporal не должен счесть её зависшей."""
    try:
        from temporalio import activity
        if activity.in_activity():
            activity.heartbeat({"waiting": "inat budget"})
    except Exception:  # noqa: BLE001
        pass


async def acquire_background() -> None:
    """Ждать, пока фоновой задаче можно сделать один запрос к iNaturalist: нет пауз и в
    общем ведре есть жетон (20 запросов в минуту на весь сервер)."""
    while True:
        if await paused(background=True):
            _heartbeat()
            wait = max(_state["shared_until"], _state["bg_until"]) - time.time()
            await asyncio.sleep(min(30.0, max(1.0, wait)))
            continue
        try:
            async with async_session() as db:
                got = (await db.execute(text("""
                    UPDATE inat_rate_state SET
                      tokens = LEAST(CAST(:cap AS double precision),
                                     tokens + extract(epoch FROM now() - updated_at) * CAST(:rate AS double precision)) - 1,
                      updated_at = now()
                    WHERE name = 'background'
                      AND LEAST(CAST(:cap AS double precision),
                                tokens + extract(epoch FROM now() - updated_at) * CAST(:rate AS double precision)) >= 1
                    RETURNING tokens"""), {"cap": BG_BURST, "rate": BG_RATE_PER_S})).first()
                await db.commit()
        except Exception as e:  # noqa: BLE001 — без базы не держим, темп ограничат паузы
            logger.warning("inat budget: token take failed: %s", type(e).__name__)
            return
        if got is not None:
            return
        _heartbeat()
        await asyncio.sleep(1.0 / BG_RATE_PER_S)
