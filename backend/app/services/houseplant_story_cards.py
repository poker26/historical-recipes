"""Рассказ о растении в карточке: разделы, «Интересное» и первая фраза.

Слой ``houseplant_story`` хранит факты с цитатами. Здесь они раскладываются по
карточкам, без вызовов модели, детерминированно, поэтому сборку можно
перезапускать сколько угодно.

Что получает карточка:

* ``story`` — разделы рассказа («Родина и жизнь в природе», «Откуда название»…),
  в каждом факты со своей книгой, годом и страницей. Их рисует новый клиент.
* ``lead_fact`` — один лучший факт. Его выпущенное приложение уже показывает
  карточкой «Интересное», поэтому рассказ виден людям без выпуска новой версии.
  Раньше там у комнатных стоял совет по поливу.
* ``verdict`` — первая фраза под названием: предупреждение об опасности, если
  оно есть, и где растение живёт в природе. Служебная фраза «Здесь собрано то,
  что о нём пишут книги…» убирается: читателю не нужно объяснять, откуда текст.

Какие факты идут в карточку. Факты о самом виде, о его роде целиком и, у
карточки рода, о видах этого рода; у фактов о другом виде подписано, о ком
они («Филодендрон имбе»). Факты о виде не переносятся на соседний вид.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.houseplant_story import STORY_KINDS

# Сколько фактов показывать в разделе: карточка не должна превращаться в дамп.
PER_KIND = 4
# Какой факт годится в «Интересное»: сначала то, что удивляет.
LEAD_ORDER = ["biology", "name", "history", "uses", "lore", "lookalikes", "homeland"]

# Служебные фразы прежней сборки (houseplant_cards.build_monograph до 20.10.2026).
_BOILERPLATE = [
    "Комнатное растение. Здесь собрано то, что о нём пишут книги по комнатному "
    "цветоводству: свет, полив, земля, пересадка и размножение.",
    "Книги описывают весь род, поэтому совет годится и для сортов.",
]


def _scope(card_latin: str, fact_latin: str | None) -> int | None:
    """Насколько факт относится к карточке: 0 — о ней самой, 1 — о роде, 2 — о виде рода.

    ``None`` — факт к карточке не относится.
    """
    if not fact_latin:
        return None
    card, fact = card_latin.lower().strip(), fact_latin.lower().strip()
    card_genus, fact_genus = card.split(" ")[0], fact.split(" ")[0]
    if fact == card:
        return 0
    if card_genus != fact_genus:
        return None
    if " " not in fact:
        return 1                      # факт о роде, карточка вида
    if " " not in card:
        return 2                      # факт о виде, карточка рода
    return None                       # соседний вид: не наш


# Подрод, секция, триба: в карточке такой факт начинался бы словами «Филодендрон
# подрод Меконостигма», а это служебная пометка систематика, не рассказ о растении.
_RANK_LABEL = re.compile(r"подрод|секци|триб|подсемейств|\bsubg|\bsect\.", re.I)


def _rank_label(row) -> bool:
    return bool(_RANK_LABEL.search(row.taxon_ru or "") or _RANK_LABEL.search(row.taxon_latin or ""))


# «Считается, что этот сорт возник как мутация…» в карточке рода: модель приписала
# роду факт о конкретном сорте, и без его названия фраза повисает (OnePlus, 20.10.2026).
_ORPHAN_REF = re.compile(r"\bэт(от|а|у|им|ого) (сорт|гибрид|культивар|вид|форм)", re.I)


def _orphan_ref(scope: int, row) -> bool:
    # Факт приписан роду целиком (в латыни одно слово): и в карточке вида, и в
    # карточке самого рода «этот сорт» в нём ни к чему не привязан.
    genus_level = " " not in (row.taxon_latin or "").strip()
    return genus_level and bool(_ORPHAN_REF.search(row.fact or ""))


def build_story(card_latin: str, card_name: str, rows) -> dict:
    """Разделы рассказа, лучший факт и фраза о родине для одной карточки."""
    picked: dict[str, list[tuple]] = {}
    seen: set[str] = set()
    scoped = [(s, r) for r in rows
              if (s := _scope(card_latin, r.taxon_latin)) is not None and not _rank_label(r)
              and not _orphan_ref(s, r)]
    for scope, row in sorted(scoped, key=lambda x: x[0]):
        key = row.fact.strip().lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        bucket = picked.setdefault(row.kind, [])
        if len(bucket) < PER_KIND:
            bucket.append((scope, row))

    blocks = []
    for kind, title in STORY_KINDS.items():
        items = picked.get(kind)
        if not items:
            continue
        facts = []
        for scope, row in items:
            about = None
            if scope == 2:
                about = row.taxon_ru or row.taxon_latin
            facts.append({"text": row.fact, "source": row.book, "year": row.year,
                          "page": row.page, "about": about})
        blocks.append({"kind": kind, "title": title, "facts": facts})

    lead = None
    for kind in LEAD_ORDER:
        for scope, row in sorted(picked.get(kind, []), key=lambda x: x[0]):
            if 40 <= len(row.fact) <= 260:
                lead = {"text": row.fact, "source": _source_label(row)}
                break
        if lead:
            break

    homeland = None
    for scope, row in sorted(picked.get("homeland", []), key=lambda x: x[0]):
        if scope < 2 and len(row.fact) <= 200:
            homeland = row.fact
            break
    return {"story": blocks, "lead": lead, "homeland": homeland,
            "facts": sum(len(b["facts"]) for b in blocks)}


def _source_label(row) -> str:
    return f"{row.book}, {row.year}" if row.year else row.book


def clean_verdict(verdict: str | None, homeland: str | None) -> str | None:
    """Первая фраза карточки без служебного текста, с родиной растения в конце."""
    v = verdict or ""
    for phrase in _BOILERPLATE:
        v = v.replace(phrase, "")
    v = " ".join(v.split())
    # Сборка перезапускается после каждой книги: фраза о родине не должна
    # дописываться второй раз.
    if homeland and homeland not in v:
        v = f"{v} {homeland}".strip()
    return v or None


def apply_story(monograph: dict, story: dict, houseplant: bool = False) -> dict:
    """Вписывает рассказ в монограф карточки. Чужие разделы не трогает.

    ``houseplant`` — растение описано книгами о комнатных, даже если карточка
    гербарная (замиокулькас, монстера, фикус). Тогда «Интересное» берётся из
    рассказа: в гербарных карточках этих растений там стояло «обогащает
    помещение аэроионами» и «убивает стрептококковую инфекцию».
    """
    m = dict(monograph)
    houseplant = houseplant or m.get("origin") == "houseplant"
    if story["story"]:
        m["story"] = story["story"]
    # «Интересное» у комнатных было советом по поливу. У гербарной карточки
    # дикорастущего растения там стоит факт из травника, его не заменяем.
    if story["lead"] and (houseplant or not m.get("lead_fact")):
        m["lead_fact"] = story["lead"]
    if houseplant or any(p in (m.get("verdict") or "") for p in _BOILERPLATE):
        m["verdict"] = clean_verdict(m.get("verdict"), story["homeland"])
        if m["verdict"] is None:
            m.pop("verdict")
    return m


async def apply_story_all(db: AsyncSession,
                          on_piece: Callable[[dict], Awaitable[None]] | None = None) -> dict:
    """Проходит по всем карточкам, о которых в слое рассказа что-то есть."""
    rows = (await db.execute(text("""
        SELECT st.taxon_latin, st.taxon_ru, st.kind, st.fact, st.page,
               s.title AS book, s.year
        FROM houseplant_story st
        JOIN houseplant_source s ON s.id = st.source_id
        WHERE st.taxon_latin IS NOT NULL
        ORDER BY st.id
    """))).all()
    by_genus: dict[str, list] = {}
    for row in rows:
        by_genus.setdefault(row.taxon_latin.split(" ")[0].lower(), []).append(row)
    if not by_genus:
        return {"cards": 0, "updated": 0}

    # Роды, которые описаны книгами о комнатных растениях.
    house_genera = set((await db.execute(text("""
        SELECT DISTINCT lower(split_part(taxon_latin, ' ', 1)) FROM houseplant_care
        WHERE latin_verified
    """))).scalars().all())

    cards = (await db.execute(text("""
        SELECT p.id, p.name, p.name_latin, m.monograph
        FROM plants p JOIN plant_reader_monograph m ON m.plant_id = p.id
        WHERE lower(split_part(p.name_latin, ' ', 1)) = ANY(:genera)
    """), {"genera": list(by_genus)})).all()

    updated = with_story = 0
    for n, card in enumerate(cards, 1):
        latin = " ".join(card.name_latin.split()[:2])
        story = build_story(latin, card.name, by_genus.get(latin.split(" ")[0].lower(), []))
        if not story["facts"]:
            continue
        with_story += 1
        stored = card.monograph if isinstance(card.monograph, dict) else json.loads(card.monograph)
        new = apply_story(stored, story, latin.split(" ")[0].lower() in house_genera)
        if new != stored:
            await db.execute(text(
                "UPDATE plant_reader_monograph SET monograph = CAST(:m AS jsonb) WHERE plant_id = :id"),
                {"id": card.id, "m": json.dumps(new, ensure_ascii=False)})
            updated += 1
        if on_piece and n % 50 == 0:
            await on_piece({"done": n, "total": len(cards), "updated": updated})

    # Служебная фраза осталась и у карточек, о которых рассказа пока нет.
    leftovers = (await db.execute(text("""
        SELECT plant_id, monograph FROM plant_reader_monograph
        WHERE monograph->>'verdict' LIKE '%Здесь собрано то, что о нём пишут книги%'
    """))).all()
    for plant_id, stored in leftovers:
        mono = dict(stored if isinstance(stored, dict) else json.loads(stored))
        verdict = clean_verdict(mono.get("verdict"), None)
        if verdict:
            mono["verdict"] = verdict
        else:
            mono.pop("verdict", None)
        await db.execute(text(
            "UPDATE plant_reader_monograph SET monograph = CAST(:m AS jsonb) WHERE plant_id = :id"),
            {"id": plant_id, "m": json.dumps(mono, ensure_ascii=False)})
    await db.commit()
    return {"facts": len(rows), "cards_checked": len(cards),
            "cards_with_story": with_story, "updated": updated,
            "boilerplate_cleaned": len(leftovers)}
