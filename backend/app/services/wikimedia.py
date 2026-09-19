"""Фотография растения из Викимедиа — второй источник после iNaturalist.

Зачем модуль. Карточка без картинки почти бесполезна: человек снял горшок и
хочет убедиться, что мы узнали именно его растение. Фотография есть у 162
карточек комнатных из 476, и за три дня из 43 показанных карточек с картинкой
были 12. iNaturalist знает дикую флору, а комнатные под книжными именами у него
встречаются редко, зато статья в Википедии есть почти про каждое: фикус,
замиокулькас, сингониум, аглаонема.

Как это работает. Русская Википедия сама переводит латинское имя в свою
статью: запрос про ``Abutilon`` приводит на «Канатник». Оттуда берётся главная
иллюстрация статьи, а по имени файла — автор и лицензия.

Почему запросы идут пачками. Первый прогон спрашивал по одному имени за раз и
на шестом запросе получил 429 — «слишком много запросов», после чего Википедия
молчала до конца прогона, и снимки нашлись у пяти карточек из 319. Справочник
умеет отвечать про полсотни страниц разом, и пачка из пятидесяти вместо
пятидесяти запросов — это то, о чём его правила и просят. Между пачками стоит
пауза, а на отказ модуль ждёт столько, сколько попросили, и повторяет.

Что мы берём и чего не берём. Только свободные лицензии: Creative Commons и
общественное достояние. Файл с несвободной лицензией пропускается целиком, даже
если картинка хороша, потому что показать её в приложении мы всё равно не
вправе. Имя автора сохраняется рядом со снимком: лицензия CC этого требует.
"""

from __future__ import annotations

import asyncio
import re

import httpx

# Википедия отвечает 403 всем, кто не представился, и просит в подписи адрес,
# по которому с нами можно связаться.
_HEADERS = {"User-Agent": "chto-rastet/1.0 (plant cards; contact via begemot26.ru)"}

# Языки по очереди: русская статья чаще ведёт к привычному русскому имени,
# английская выручает там, где русской статьи про вид ещё нет.
_WIKIS = ("ru.wikipedia.org", "en.wikipedia.org")

# Сколько имён спрашиваем за раз. Пятьдесят — предел для незалогиненного клиента.
BATCH = 50

# Пауза между пачками. Спешить некуда: прогон живёт в воркере.
PAUSE = 1.0

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


def attribution_from(meta: dict) -> str:
    """Кого назвать под снимком: автора и лицензию."""
    author = plain((meta.get("Artist") or {}).get("value", ""))
    short = plain((meta.get("LicenseShortName") or {}).get("value", ""))
    short = short or plain((meta.get("License") or {}).get("value", ""))
    return ", ".join(x for x in (author, short, "Викимедиа") if x)


def file_key(name: str) -> str:
    """Имя файла в одном написании.

    Справочник называет один и тот же файл двумя способами: в статье он
    «Hoya_carnosa_20080928.jpg», а в заголовке страницы файла — «Файл:Hoya
    carnosa 20080928.jpg», через пробелы. Из-за этого расхождения лицензии не
    находились почти ни к одному снимку: сопоставление шло по разным написаниям.
    """
    return (name or "").replace("_", " ").strip()


def title_map(payload: dict) -> dict[str, str]:
    """Кто во что превратился: справочник правит написание и ведёт по редиректам.

    Спросили про ``Abutilon``, а страница называется «Канатник». Без этой карты
    ответ не разложить обратно по именам, которые мы спрашивали.
    """
    query = payload.get("query", {})
    out: dict[str, str] = {}
    for step in ("normalized", "redirects"):
        for item in query.get(step, []) or []:
            out[item["from"]] = item["to"]

    # Цепочка бывает длиннее одного шага: сначала правка написания, потом редирект.
    resolved: dict[str, str] = {}
    for start in out:
        current = start
        for _ in range(4):
            nxt = out.get(current)
            if not nxt or nxt == current:
                break
            current = nxt
        resolved[start] = current
    return resolved


async def _ask(client: httpx.AsyncClient, host: str, params: dict) -> dict:
    """Один запрос к справочнику, с уважением к отказу «слишком часто».

    При 429 ждём столько, сколько попросили в ответе, и пробуем снова. Три
    отказа подряд — сдаёмся и возвращаем пустоту: отсутствие фотографии это то,
    как карточка выглядит сейчас, хуже не станет.
    """
    for attempt in range(3):
        try:
            response = await client.get(f"https://{host}/w/api.php", params=params,
                                        headers=_HEADERS)
        except Exception:
            return {}
        if response.status_code == 429:
            wait = response.headers.get("Retry-After")
            await asyncio.sleep(float(wait) if (wait or "").isdigit() else 30.0 * (attempt + 1))
            continue
        try:
            return response.json()
        except Exception:
            return {}
    return {}


async def _images_for_titles(client: httpx.AsyncClient, host: str,
                             titles: list[str]) -> dict[str, dict]:
    """Главные иллюстрации статей: ``{имя, о котором спрашивали: сведения}``."""
    payload = await _ask(client, host, {
        "action": "query", "format": "json", "redirects": "1",
        "titles": "|".join(titles), "prop": "pageimages", "piprop": "original|name",
    })
    pages = payload.get("query", {}).get("pages", {})
    if not pages:
        return {}

    renamed = title_map(payload)
    by_title = {}
    for page in pages.values():
        file_name = page.get("pageimage")
        source = (page.get("original") or {}).get("source")
        if file_name and source:
            by_title[page.get("title")] = {"file": file_name, "source": source,
                                           "article": page.get("title")}

    out: dict[str, dict] = {}
    for asked in titles:
        found = by_title.get(renamed.get(asked, asked)) or by_title.get(asked)
        if found:
            out[asked] = found
    return out


async def _licenses_for_files(client: httpx.AsyncClient, host: str,
                              files: list[str]) -> dict[str, dict]:
    """Автор и лицензия по именам файлов."""
    payload = await _ask(client, host, {
        "action": "query", "format": "json",
        "titles": "|".join(f"File:{name}" for name in files),
        "prop": "imageinfo", "iiprop": "extmetadata|url",
    })
    pages = payload.get("query", {}).get("pages", {})
    out: dict[str, dict] = {}
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        name = file_key((page.get("title") or "").split(":", 1)[-1])
        out[name] = {
            "license": (meta.get("License") or {}).get("value", ""),
            "attribution": attribution_from(meta),
            "url": info.get("url", ""),
        }
    return out


async def photos_for_latins(client: httpx.AsyncClient, latins: list[str]) -> dict[str, dict]:
    """Снимки для списка имён. Спрашиваем пачками и ходим по языкам по очереди."""
    left = list(latins)
    out: dict[str, dict] = {}

    for host in _WIKIS:
        if not left:
            break
        still_left: list[str] = []
        for start in range(0, len(left), BATCH):
            chunk = left[start:start + BATCH]
            images = await _images_for_titles(client, host, chunk)
            await asyncio.sleep(PAUSE)

            if images:
                files = sorted({item["file"] for item in images.values()})
                licenses: dict[str, dict] = {}
                for at in range(0, len(files), BATCH):
                    licenses.update(await _licenses_for_files(client, host, files[at:at + BATCH]))
                    await asyncio.sleep(PAUSE)
            else:
                licenses = {}

            for asked in chunk:
                item = images.get(asked)
                meta = licenses.get(file_key(item["file"])) if item else None
                if not item or not meta or not license_is_free(meta["license"]):
                    still_left.append(asked)
                    continue
                out[asked] = {
                    "photo_url": meta["url"] or item["source"],
                    "photo_attribution": meta["attribution"],
                    "license": meta["license"],
                    "article": item["article"],
                    "wiki": host,
                }
        left = still_left

    return out


async def photo_for_latin(client: httpx.AsyncClient, latin: str) -> dict | None:
    """Снимок для одного имени. Нужен для проверки руками, прогон ходит пачками."""
    found = await photos_for_latins(client, [latin])
    return found.get(latin)


async def fill_card_photos(db, limit: int = 0, apply: bool = False,
                           on_piece=None) -> dict:
    """Ставит фотографии карточкам, у которых их нет.

    Берём карточки слоя комнатных: их снимают прямо сейчас, и именно у них дыра.
    Имя для поиска — латынь карточки, потому что статья Википедии заводится на
    неё, а русские имена у книг и у энциклопедии расходятся («Плющ восковой» у
    Саакова против хойи мясистой в статье).
    """
    from sqlalchemy import text

    rows = (await db.execute(text(
        "SELECT id, name, name_latin FROM plants "
        "WHERE origin = 'houseplant' AND (photo_url IS NULL OR photo_url = '') "
        "AND name_latin IS NOT NULL AND name_latin <> '' ORDER BY name_latin"
    ))).all()
    if limit:
        rows = rows[:limit]

    out = {"looked_at": len(rows), "found": 0, "no_photo": 0}
    by_latin = {row.name_latin: row.id for row in rows}

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        names = list(by_latin)
        for start in range(0, len(names), BATCH):
            chunk = names[start:start + BATCH]
            found = await photos_for_latins(client, chunk)
            for latin, photo in found.items():
                out["found"] += 1
                if apply:
                    await db.execute(text(
                        "UPDATE plants SET photo_url = :url, photo_attribution = :who "
                        "WHERE id = :id"),
                        {"url": photo["photo_url"], "who": photo["photo_attribution"],
                         "id": by_latin[latin]})
            if apply:
                await db.commit()
            out["no_photo"] += len(chunk) - len(found)

            if on_piece:
                await on_piece({"done": min(start + BATCH, len(names)),
                                "total": len(names), "found": out["found"]})

    return out
