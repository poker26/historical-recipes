"""Пилот извлекателя ухода: одна статья книги, глазами человека.

Запуск на проде, где лежит ключ к модели:

    cat backend/scripts/houseplant_pilot.py \\
      | docker compose exec -T backend python - \\
        --book hessayon-vse-o-komnatnykh-rasteniiakh.pdf --pages 54

Скрипт берёт книгу из бакета ``houseplant-books``, вынимает текст указанных
страниц (или распознаёт их, если текстового слоя нет), зовёт извлекатель и
печатает то, что доехало: поле, сезон, нормализованное значение, фразу для
читателя и цитату со страницей. Ничего не пишет в базу: пилот нужен, чтобы
сверить извлечённое со сканом собственными глазами, а не чтобы наполнить её.
"""

import argparse
import asyncio
import io
import json
import sys

import fitz

from app.services.houseplant_care import extract_article
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



def _parse_pages(spec: str) -> list[int]:
    """«54», «224-226», «54,120» — всё это допустимые способы назвать страницы."""
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
    """Текст страницы.

    У Саакова и Хессайона текстовый слой есть, у Головкина и Воронцова его нет
    вовсе, поэтому страница без слоя распознаётся тем же путём, каким корпус
    разбирает травники: сначала tesseract, при плохом качестве зрячая модель.
    """
    text = doc[number - 1].get_text().strip()
    if len(text) > 200:
        return text

    from app.services.ocr import ocr_page_with_fallback   # импорт нужен редко

    pixmap = page_pixmap(doc[number - 1])
    recognized, confidence, engine = await ocr_page_with_fallback(pixmap.tobytes("png"))
    print(f"  стр. {number}: текстового слоя нет, распознано через {engine} "
          f"(уверенность {confidence:.0f})", file=sys.stderr)
    return recognized


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", required=True, help="имя объекта в бакете houseplant-books")
    parser.add_argument("--pages", required=True, help="страницы книги, например 54 или 224-226")
    parser.add_argument("--json", action="store_true", help="выдать JSON вместо читаемого отчёта")
    args = parser.parse_args()

    response = get_client().get_object(BUCKET, args.book)
    try:
        doc = fitz.open(stream=io.BytesIO(response.read()), filetype="pdf")
    finally:
        response.close()
        response.release_conn()

    pages = _parse_pages(args.pages)
    texts = [await _page_text(doc, number) for number in pages]
    article = "\n".join(texts)
    if not article.strip():
        print("На этих страницах текста не нашлось.")
        return 1

    plants = await extract_article(article, page=pages[0])

    if args.json:
        print(json.dumps([{
            "taxon": plant["taxon"],
            "care": [vars(f) for f in plant["care"]],
            "problems": [vars(p) for p in plant["problems"]],
        } for plant in plants], ensure_ascii=False, indent=2))
        return 0

    print(f"Книга: {args.book}, страницы {args.pages}")
    print(f"Символов в тексте: {len(article)}")
    print(f"Растений на странице: {len(plants)}")

    for plant in plants:
        taxon = plant["taxon"]
        print(f"\n=== {taxon.get('ru') or '?'} — {taxon.get('latin') or '?'} "
              f"({taxon.get('rank') or 'ранг не назван'})")

        print(f"Уход, утверждений {len(plant['care'])}:")
        for fact in plant["care"]:
            season = {"summer": "лето", "winter": "зима"}.get(fact.season or "", "круглый год")
            marks = []
            if fact.greenhouse:
                marks.append("оранжерея")
            if fact.reference:
                marks.append(f"ссылка: {fact.reference}")
            tail = f"   [{', '.join(marks)}]" if marks else ""
            print(f"  • {fact.field_name} / {season}: {fact.value_text}")
            print(f"    значение: {json.dumps(fact.value, ensure_ascii=False)}{tail}")
            print(f"    цитата (стр. {fact.page}): {fact.quote[:160]}")

        if plant["problems"]:
            print(f"Болезни и вредители, записей {len(plant['problems'])}:")
            for problem in plant["problems"]:
                print(f"  • {problem.kind}: {problem.name}")
                if problem.symptom:
                    print(f"    признак: {problem.symptom}")
                if problem.remedy:
                    print(f"    что делать: {problem.remedy}")
                if problem.chemicals:
                    print(f"    препараты: {', '.join(problem.chemicals)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
