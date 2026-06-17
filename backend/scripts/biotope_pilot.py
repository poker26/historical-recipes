"""DRY pilot: LLM multi-label canonicalization of plant_habitats.biotope free-text
to a controlled biotope vocabulary. Prints mapping only — writes nothing."""
import asyncio
import sys

from sqlalchemy import text

from app.database import async_session
from app.services.llm import chat_completion_json

VOCAB = [
    "лес", "лес лиственный", "лес хвойный", "лес смешанный",
    "опушки/поляны/вырубки/редколесье",
    "луг", "степь", "поле/сорное",
    "болото/сырое", "берега водоёмов", "водное/прибрежное",
    "каменистые/скалистые склоны", "пески/дюны/обнажения",
    "горы/предгорья", "сады/парки", "кустарники/заросли",
]
_SYS = ("Ты — фито-эколог. По описанию местообитания растения возвращаешь СТРОГО "
        "валидный JSON со списком подходящих биотопов ТОЛЬКО из заданного словаря "
        "(мульти-лейбл; ничего вне словаря).\n"
        "ПРАВИЛА: (1) регион/страну/названия мест ИГНОРИРУЙ — это не биотоп. Если в "
        "описании ТОЛЬКО названия мест/населённых пунктов без среды обитания — верни []. "
        "(2) Лес: если указан ТИП (лиственный/хвойный/смешанный) — бери конкретный подтип; "
        "если просто «лес/леса» без уточнения — бери общий «лес» (НЕ перечисляй все подтипы).")


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT biotope FROM plant_habitats WHERE biotope IS NOT NULL "
            "AND length(biotope) > 8 ORDER BY random() LIMIT :n"), {"n": n})).all()
    voc = ", ".join(f'"{v}"' for v in VOCAB)
    for (bio,) in rows:
        prompt = (f"Словарь биотопов: [{voc}].\n"
                  f"Описание: «{bio}»\n"
                  'Верни JSON: {"biotopes": ["...", ...]} — только из словаря.')
        try:
            out = await chat_completion_json(
                [{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
                task="lightweight", temperature=0.1, max_tokens=300)
            tags = out.get("biotopes") if isinstance(out, dict) else None
        except Exception as e:
            tags = f"ERR {str(e)[:40]}"
        print(f"\n«{bio[:80]}»\n  → {tags}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
