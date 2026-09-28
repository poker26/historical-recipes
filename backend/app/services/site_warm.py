"""Прогрев сайта botanik.fun: обход адресов из карты сайта.

Сайт рендерит страницы на запрос, а данные бэкенда держит в кэше Next. Первый заход на
страницу после выкладки или после долгого простоя идёт в бэкенд и бывает медленным (замер
27.09: холодная карточка 3–8 секунд). Прогретая запись кэша дальше отдаётся сразу, а по
истечении срока обновляется в фоне. Поэтому один спокойный обход всех адресов из карты
сайта делает быстрыми и следующие заходы, в том числе робота поисковика.

Обход идёт внутри сети compose (``http://site:3000``), медленно, в несколько потоков, и
заодно меряет время ответа: самые медленные адреса в сводке показывают, какие запросы
бэкенда стоит ускорять.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

SITE_INTERNAL = os.getenv("SITE_INTERNAL_URL", "http://site:3000")
SITE_PUBLIC = "https://botanik.fun"
_LOC = re.compile(r"<loc>([^<]+)</loc>")


async def sitemap_urls(client: httpx.AsyncClient, only: list[str] | None = None) -> list[str]:
    """Все адреса из карт сайта, в порядке файлов индекса (static, books, …)."""
    idx = (await client.get(f"{SITE_INTERNAL}/sitemap.xml")).text
    names = re.findall(r"/sitemaps/([^<]+)\.xml", idx)
    if only:
        names = [n for n in names if any(n.startswith(p) for p in only)]
    urls: list[str] = []
    for n in names:
        r = await client.get(f"{SITE_INTERNAL}/sitemaps/{n}.xml")
        urls += [html.unescape(u) for u in _LOC.findall(r.text)]
    return urls


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(len(s) * p))], 2)


async def run_site_warm(limit: int = 0, rps: float = 2.0, concurrency: int = 2,
                        only: list[str] | None = None, skip: int = 0,
                        progress: Callable[[dict], None] | None = None) -> dict:
    """Обойти адреса карты сайта. ``limit`` для пробного прогона, ``rps`` потолок запросов
    в секунду, ``skip`` сколько адресов пропустить с начала (продолжение после перезапуска)."""
    timeout = httpx.Timeout(90.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                 headers={"User-Agent": "botanik-warm/1.0"}) as client:
        urls = await sitemap_urls(client, only)
        total_all = len(urls)
        urls = urls[skip:]
        if limit:
            urls = urls[:limit]
        stats = {"total": total_all, "planned": len(urls), "done": skip, "ok": 0, "errors": 0,
                 "status": {}}
        times: list[float] = []
        slow: list[tuple[float, str]] = []
        sem = asyncio.Semaphore(concurrency)
        gap = 1.0 / max(rps, 0.1)
        next_at = time.monotonic()
        lock = asyncio.Lock()

        async def one(u: str) -> None:
            nonlocal next_at
            async with lock:  # равномерный темп: не больше rps запросов в секунду
                now = time.monotonic()
                wait = next_at - now
                next_at = max(now, next_at) + gap
            if wait > 0:
                await asyncio.sleep(wait)
            async with sem:
                t0 = time.monotonic()
                code = "error"
                try:
                    r = await client.get(u.replace(SITE_PUBLIC, SITE_INTERNAL, 1))
                    code = str(r.status_code)
                except Exception as e:  # noqa: BLE001 — один сбой не останавливает обход
                    logger.warning("warm %s: %s", u, type(e).__name__)
                dt = time.monotonic() - t0
            stats["status"][code] = stats["status"].get(code, 0) + 1
            if code == "200":
                stats["ok"] += 1
                times.append(dt)
                slow.append((dt, u))
                if len(slow) > 200:
                    slow.sort(reverse=True)
                    del slow[40:]
            else:
                stats["errors"] += 1
            stats["done"] += 1
            if progress and stats["done"] % 25 == 0:
                progress({k: v for k, v in stats.items() if k != "status"})

        # Пачками, чтобы не держать в памяти десятки тысяч задач.
        for i in range(0, len(urls), 200):
            await asyncio.gather(*(one(u) for u in urls[i:i + 200]))

        slow.sort(reverse=True)
        stats.update({
            "p50_s": _pct(times, 0.5), "p90_s": _pct(times, 0.9), "p99_s": _pct(times, 0.99),
            "slowest": [{"s": round(t, 2), "url": u} for t, u in slow[:25]],
        })
        logger.info("site warm done: %s", {k: v for k, v in stats.items() if k != "slowest"})
        return stats
