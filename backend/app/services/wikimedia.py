"""Фотография растения из Викимедиа — второй источник после iNaturalist.

Зачем модуль. Карточка без картинки почти бесполезна: человек снял горшок и
хочет убедиться, что мы узнали именно его растение. Сейчас фотография есть у
175 карточек комнатных из 621, и за три дня из 43 показанных карточек с
картинкой были 12. iNaturalist знает дикую флору, а комнатные под книжными
именами у него встречаются редко, зато статья в Википедии есть почти про
каждое: фикус, замиокулькас, сингониум, аглаонема.

Как это работает. Русская Википедия сама переводит латинское имя в свою
статью: запрос про ``Ficus elastica`` приводит на «Фикус каучуконосный». Оттуда
берётся главная иллюстрация статьи, а по имени файла — автор и лицензия.

Что мы берём и чего не берём. Только свободные лицензии: Creative Commons и
общественное достояние. Файл с несвободной лицензией пропускается целиком, даже
если картинка хороша, потому что показать её в приложении мы всё равно не
вправе. Имя автора сохраняется рядом со снимком: лицензия CC этого требует.
"""

from __future__ import annotations

import re

import httpx

_HEADERS = {"User-Agent": "chto-rastet/1.0 (plant cards; contact via begemot26.ru)"}

# Языки по очереди: русская статья чаще ведёт к привычному русскому имени,
# английская выручает там, где русской статьи про вид ещё нет.
_WIKIS = ("ru.wikipedia.org", "en.wikipedia.org")

# Свободные лицензии. Всё остальное — мимо, включая «добросовестное
# использование»: оно разрешает иллюстрировать энциклопедию, а не наше приложение.
_FREE_LICENSE_RE = re.compile(r"^(cc[-\s]?(by|zero|0)|cc0|pd|public\s*domain)", re.I)

_TAG_RE = re.compile(r"<[^>]+>")


def license_is_free(code: str) -> bool:
    """Лицензию можно показывать в приложении."""
    return bool(_FREE_LICENSE_RE.match((code or "").strip()))


def plain(value: str) -> str:
    """Подпись без разметки: Викимедиа отдаёт автора куском HTML."""
    return _TAG_RE.sub("", value or "").replace("&amp;", "&").strip()


async def photo_for_latin(client: httpx.AsyncClient, latin: str) -> dict | None:
    """Снимок для латинского имени: адрес, автор, лицензия и имя статьи.

    Возвращает ``None``, когда статьи нет, иллюстрации в ней нет или лицензия
    несвободная. Сетевая ошибка тоже даёт ``None``: отсутствие фотографии — это
    то, как карточка выглядит сейчас, хуже не станет.
    """
    for host in _WIKIS:
        try:
            first = await client.get(f"https://{host}/w/api.php", params={
                "action": "query", "format": "json", "redirects": "1",
                "titles": latin, "prop": "pageimages", "piprop": "original|name",
            }, headers=_HEADERS)
            pages = first.json().get("query", {}).get("pages", {})
        except Exception:
            continue

        for page in pages.values():
            file_name = page.get("pageimage")
            source = (page.get("original") or {}).get("source")
            if not file_name or not source:
                continue

            try:
                second = await client.get(f"https://{host}/w/api.php", params={
                    "action": "query", "format": "json",
                    "titles": f"File:{file_name}",
                    "prop": "imageinfo", "iiprop": "extmetadata|url",
                }, headers=_HEADERS)
                info = list(second.json()["query"]["pages"].values())[0]["imageinfo"][0]
            except Exception:
                continue

            meta = info.get("extmetadata") or {}
            code = (meta.get("License") or {}).get("value", "")
            if not license_is_free(code):
                continue

            author = plain((meta.get("Artist") or {}).get("value", ""))
            short = plain((meta.get("LicenseShortName") or {}).get("value", "")) or code
            attribution = ", ".join(x for x in (author, short, "Викимедиа") if x)
            return {
                "photo_url": info.get("url") or source,
                "photo_attribution": attribution,
                "license": code,
                "article": page.get("title"),
                "wiki": host,
            }
    return None


async def photos_for_latins(latins: list[str]) -> dict[str, dict]:
    """Снимки для списка имён, по одному запросу за именем.

    Идём по очереди, а не всем скопом: Викимедиа просит не бить залпами, а
    выигрыш от спешки тут никакой — прогон и так живёт в воркере.
    """
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for latin in latins:
            found = await photo_for_latin(client, latin)
            if found:
                out[latin] = found
    return out


async def fill_card_photos(db, limit: int = 0, apply: bool = False,
                           on_piece=None) -> dict:
    """Ставит фотографии карточкам, у которых их нет.

    Берём только карточки слоя комнатных: их снимают прямо сейчас, и именно у
    них дыра. Имя для поиска — латынь карточки, потому что статья Википедии
    заводится на неё, а русские имена у книг и у энциклопедии расходятся
    («Плющ восковой» у Саакова против «Хойи мясистой» в статье).
    """
    from sqlalchemy import text

    rows = (await db.execute(text(
        "SELECT id, name, name_latin FROM plants "
        "WHERE origin = 'houseplant' AND (photo_url IS NULL OR photo_url = '') "
        "AND name_latin IS NOT NULL AND name_latin <> '' ORDER BY name_latin"
    ))).all()
    if limit:
        rows = rows[:limit]

    out = {"looked_at": len(rows), "found": 0, "not_free": 0, "no_article": 0}

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for number, row in enumerate(rows, start=1):
            found = await photo_for_latin(client, row.name_latin)
            if found:
                out["found"] += 1
                if apply:
                    await db.execute(text(
                        "UPDATE plants SET photo_url = :url, photo_attribution = :who "
                        "WHERE id = :id"),
                        {"url": found["photo_url"], "who": found["photo_attribution"],
                         "id": row.id})
                    await db.commit()
            else:
                out["no_article"] += 1

            if on_piece:
                await on_piece({"done": number, "total": len(rows),
                                "latin": row.name_latin, "found": out["found"]})

    return out
