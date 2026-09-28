"""Отправить адреса botanik.fun в IndexNow, чтобы Яндекс узнал о новых и изменённых
страницах сразу, а не при следующем обходе. Яндекс делится адресами с другими
поисковиками протокола IndexNow.

Ключ сайта лежит в .env (INDEXNOW_KEY), сайт отдаёт его по адресу /indexnow.txt: так
Яндекс проверяет, что адреса присылает владелец. Скрипт берёт ключ оттуда же.

    # сколько адресов уйдёт, без отправки
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/indexnow_submit.py --dry-run
    # отправить всё из карты сайта (пачками по 10 000)
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/indexnow_submit.py
    # только часть карт, например новые подборки и книги
    docker compose exec -T -e PYTHONPATH=/app backend python /app/scripts/indexnow_submit.py --only static,books
"""

import argparse
import asyncio

import httpx

from app.services.site_warm import SITE_INTERNAL, sitemap_urls

HOST = "botanik.fun"
ENDPOINT = "https://yandex.com/indexnow"
BATCH = 10000


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="только посчитать адреса")
    parser.add_argument("--only", default="", help="карты с такими префиксами имени, через запятую")
    parser.add_argument("--limit", type=int, default=0, help="не больше стольких адресов")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        r = await client.get(f"{SITE_INTERNAL}/indexnow.txt")
        key = r.text.strip() if r.status_code == 200 else ""
        if not key:
            print("Сайт не отдаёт ключ /indexnow.txt: проверь INDEXNOW_KEY в .env и пересоздай site.")
            return 1
        only = [x.strip() for x in args.only.split(",") if x.strip()] or None
        urls = await sitemap_urls(client, only)
        if args.limit:
            urls = urls[:args.limit]
        print(f"адресов к отправке: {len(urls)}")
        if args.dry_run:
            for u in urls[:5]:
                print("  ", u)
            return 0
        for i in range(0, len(urls), BATCH):
            body = {"host": HOST, "key": key, "keyLocation": f"https://{HOST}/indexnow.txt",
                    "urlList": urls[i:i + BATCH]}
            resp = await client.post(ENDPOINT, json=body)
            print(f"  пачка {i // BATCH + 1}: {len(body['urlList'])} адресов, ответ {resp.status_code} {resp.text[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
