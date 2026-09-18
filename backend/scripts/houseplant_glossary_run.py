"""Разбор вводных глав книги: что значит «уход общий» и «земельная смесь № 2».

Головкин пишет уход ссылками: статья про вид говорит «Содержат зимой при
температуре 10—14 °C. Уход общий. Земельная смесь № 2», а расшифровка стоит в
начале книги. Пока эти ссылки не разрешены, карточка покажет человеку слова,
за которыми для него ничего нет.

Скрипт читает указанные страницы, просит модель найти там определения и
нумерованные составы земли, и складывает их в словарь при источнике. Ключи
пишутся ровно в том виде, в каком их находит извлекатель в статьях: «уход
общий», «земельная смесь № 2».

Запуск на проде:

    docker compose exec -T backend python /app/houseplant_glossary_run.py \\
        --source golovkin1989 --pages 20-46 --apply

Без ``--apply`` печатает найденное и ничего не пишет.
"""

import argparse
import asyncio
import io
import sys

import fitz
from sqlalchemy import text

from app.database import async_session
from app.services.houseplant_care import (
    HOUSEPLANT_BOOKS,
    canonical_reference_key,
    describe_soil_mix,
    normalize_text,
    quote_is_grounded,
)
from app.services.llm import chat_completion_json
from app.services.minio import get_client

BUCKET = "houseplant-books"

# Крупные сканы приходится уменьшать. Головкин снят с разрешением около 1800
# точек на дюйм, и страница, отрисованная в 300, весит столько, что зрячая
# модель отказывается её принимать («Multimodal file size is too large»), а
# распознавание молча остаётся на голом tesseract. Поэтому масштаб считается
# от самой страницы: длинная сторона не длиннее 2200 точек.
MAX_PAGE_SIDE = 2200


def page_pixmap(page, dpi: int = 300):
    """Отрисовывает страницу так, чтобы она прошла к зрячей модели."""
    rect = page.rect
    longest = max(rect.width, rect.height) * dpi / 72
    if longest > MAX_PAGE_SIDE:
        dpi = max(120, int(dpi * MAX_PAGE_SIDE / longest))
    return page.get_pixmap(dpi=dpi)


_PROMPT = """Ты читаешь вводную часть книги про комнатные растения.

Найди в тексте определения, на которые книга потом ссылается в статьях о
растениях. Это два вида записей:

1. Что автор называет общим уходом (полив, опрыскивание, свет, подкормка).
2. Нумерованные составы земли: «земельная смесь № 1», «№ 2» и так далее, с
   долями дерновой, листовой, перегнойной земли, торфа и песка.

Верни СТРОГО JSON:
{
  "entries": [
    {"key": "уход общий" | "земельная смесь № N",
     "body": "<что это значит, словами книги, одним абзацем>",
     "quote": "<дословный кусок текста, слово в слово>"}
  ]
}

Если таких определений на странице нет, верни пустой список. Ничего не
придумывай: без дословной цитаты запись не нужна.

Текст:
---
{page}
---"""

# Земельные смеси Головкина живут не текстом, а таблицей на странице 22: восемь
# пронумерованных строк и шесть столбцов (дерновая, листовая и хвойная земля,
# перегной, торф, песок). Обычное распознавание рассыпает такую таблицу по
# строкам и путает столбцы, поэтому её читает зрячая модель, и просят у неё
# сразу разложенный состав.
_SOIL_TABLE_PROMPT = """На изображении таблица земельных смесей из книги о комнатных растениях.

Столбцы: дерновая земля, листовая земля, хвойная земля, перегной, торф, песок.
Строки пронумерованы: № 1, № 2 и так далее. В клетках объёмные части; прочерк
означает, что этого компонента в смеси нет. В некоторых строках есть приписка
справа (например «+ сухой коровяк», «+ 2 части сфагнума») — сохрани её.

Верни СТРОГО JSON:
{
  "mixes": [
    {"number": 1,
     "parts": {"дерновая земля": "2", "листовая земля": "1", "перегной": "1",
                "торф": "1", "песок": "1"},
     "note": "<приписка справа или пустая строка>"}
  ]
}

Компоненты с прочерком не включай. Ничего не додумывай: если клетка не
читается, пропусти её."""


async def read_soil_table(doc, number: int) -> dict[str, dict]:
    """Читает таблицу смесей со страницы и превращает её в записи словаря."""
    from app.services.ocr import ocr_page_llm

    pixmap = page_pixmap(doc[number - 1])
    raw = await ocr_page_llm(pixmap.tobytes("png"), task="ocr_hard")
    print(f"  стр. {number}: таблица прочитана зрячей моделью", file=sys.stderr)

    data = await chat_completion_json(
        [{"role": "user", "content": f"{_SOIL_TABLE_PROMPT}\n\nРаспознанный текст:\n{raw}"}],
        task="plant_extraction", temperature=0.1,
    )
    found: dict[str, dict] = {}
    for mix in (data or {}).get("mixes", []) if isinstance(data, dict) else []:
        try:
            key = f"земельная смесь № {int(mix.get('number'))}"
        except (TypeError, ValueError):
            continue
        body = describe_soil_mix(mix)
        if len(body) < 12:          # пустая строка таблицы советом не является
            continue
        found[key] = {"body": body, "quote": raw[:400], "page": number}
        print(f"  {key}: {body}")
    return found


def _parse_pages(spec: str) -> list[int]:
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            first, last = chunk.split("-", 1)
            pages.extend(range(int(first), int(last) + 1))
        elif chunk:
            pages.append(int(chunk))
    return pages


async def _page_text(doc: "fitz.Document", number: int) -> str:
    body = doc[number - 1].get_text().strip()
    if len(body) > 200:
        return body

    from app.services.ocr import ocr_page_with_fallback

    pixmap = page_pixmap(doc[number - 1])
    recognized, confidence, engine = await ocr_page_with_fallback(pixmap.tobytes("png"))
    print(f"  стр. {number}: распознано через {engine} (уверенность {confidence:.0f})",
          file=sys.stderr)
    return recognized


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--book", required=True, help="объект в бакете houseplant-books")
    parser.add_argument("--pages", help="страницы вводной части, например 20-46")
    parser.add_argument("--soil-table", type=int, default=0,
                        help="страница с таблицей земельных смесей (у Головкина 22)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    response = get_client().get_object(BUCKET, args.book)
    try:
        doc = fitz.open(stream=io.BytesIO(response.read()), filetype="pdf")
    finally:
        response.close()
        response.release_conn()

    found: dict[str, dict] = {}
    if args.soil_table:
        found.update(await read_soil_table(doc, args.soil_table))

    for number in _parse_pages(args.pages) if args.pages else []:
        body = await _page_text(doc, number)
        if not body.strip():
            continue
        try:
            data = await chat_completion_json(
                [{"role": "user", "content": _PROMPT.replace("{page}", body)}],
                task="plant_extraction", temperature=0.1,
            )
        except Exception as error:
            print(f"  стр. {number}: разбор не удался — {error}")
            continue

        for entry in (data or {}).get("entries", []) if isinstance(data, dict) else []:
            key = canonical_reference_key(entry.get("key") or "")
            quote = (entry.get("quote") or "").strip()
            if not key or not quote_is_grounded(quote, body):
                continue
            if key in found:            # первое вхождение и есть определение
                continue
            found[key] = {"body": normalize_text(entry.get("body") or quote),
                          "quote": quote, "page": number}
            print(f"  стр. {number}: {key} — {found[key]['body'][:120]}")

    print(f"\nНайдено записей словаря: {len(found)}")
    if not args.apply:
        print("Ничего не записано: для записи нужен --apply")
        return 0

    async with async_session() as db:
        # Словарь может прийти раньше самой книги: у Головкина ссылки нужно
        # разрешить до того, как его статьи попадут в базу. Поэтому запись о
        # книге заводим здесь же, если её ещё нет.
        meta = dict(HOUSEPLANT_BOOKS.get(args.source) or {})
        await db.execute(text("""
            INSERT INTO houseplant_source (id, title, author, year, minio_object, audience)
            VALUES (:id, :title, :author, :year, :object, :audience)
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": args.source,
            "title": meta.get("title") or args.source,
            "author": meta.get("author"),
            "year": meta.get("year"),
            "object": meta.get("object") or args.book,
            "audience": meta.get("audience") or "room",
        })

        for key, entry in found.items():
            await db.execute(text("""
                INSERT INTO houseplant_glossary (source_id, key, body, quote, page)
                VALUES (:source, :key, :body, :quote, :page)
                ON CONFLICT (source_id, key) DO UPDATE
                    SET body = EXCLUDED.body, quote = EXCLUDED.quote, page = EXCLUDED.page
            """), {"source": args.source, "key": key, "body": entry["body"],
                   "quote": entry["quote"], "page": entry["page"]})
        await db.commit()
    print(f"Записано в словарь источника {args.source}: {len(found)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
