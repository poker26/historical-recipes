"""Слияние карточек, которые мы сами развели по двум записям.

Откуда взялись дубли. Сборщик карточек комнатных искал в гербарии запись по
чистому латинскому имени. У гербарной записи имя было с фамилией автора —
«Ficus elastica Roxb.», — поэтому сборщик её не находил и заводил свою,
«Ficus elastica». Так на одно растение стало две карточки: одна с показами и
историей, вторая с уходом из книг.

Кому от этого хуже. Человек снимает фикус и попадает на гербарную карточку: 32
показа за месяц. Уход при этом лежит на второй, которую никто не открывает.

Что делает слияние. Оставляет ту карточку, которую люди видят, переносит на неё
уход и снимок, а пустышку убирает. Победитель определяется показами и
содержимым, а не тем, кто завёл запись раньше.

Чего слияние не делает. Не трогает имя: «Резиновое дерево» против «Фикуса
каучуконосного» — это спор о том, как растение правильно назвать, и решать его
механическим правилом нельзя. Не сливает пары, где гербарных карточек несколько:
там сначала надо разобраться внутри гербария.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from app.services.card_latin import candidate_latin

CARDS = """
SELECT p.id, p.name, p.name_latin, p.origin, p.photo_url, p.photo_attribution,
       (SELECT count(*) FROM identifications i WHERE i.matched_plant_id = p.id) AS shots,
       (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id = p.id) AS uses
FROM plants p WHERE p.name_latin IS NOT NULL AND p.name_latin <> ''
"""


def find_pairs(rows) -> list[tuple]:
    """Пары «одна карточка комнатного плюс одна гербарная» с общим чистым именем."""
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        key = (candidate_latin(row.name_latin) or row.name_latin).lower()
        groups[key].append(row)

    pairs = []
    for cards in groups.values():
        houseplants = [c for c in cards if c.origin == "houseplant"]
        herbarium = [c for c in cards if c.origin == "herbarium"]
        if len(houseplants) == 1 and len(herbarium) == 1:
            pairs.append((herbarium[0], houseplants[0]))
    return pairs


async def merge_houseplant_duplicates(db, apply: bool = False, on_piece=None) -> dict:
    """Сводит такие пары в одну карточку.

    Уход переезжает на ту запись, которую открывают люди; снимок — только если
    у неё своего нет. Дубль удаляется, и вместе с ним уходит его монограф: он
    соберётся заново уже на живой карточке.
    """
    rows = (await db.execute(text(CARDS))).all()
    pairs = find_pairs(rows)

    out = {"pairs": len(pairs), "merged": 0, "care_moved": 0, "photos_moved": 0,
           "examples": []}

    for number, (keep, drop) in enumerate(pairs, start=1):
        # Победитель — тот, кого видят люди. Если показов нет ни у кого, пусть
        # остаётся гербарная запись: она старше и с ней связан остальной корпус.
        if drop.shots > keep.shots and drop.uses >= keep.uses:
            keep, drop = drop, keep

        if apply:
            moved = (await db.execute(text(
                "UPDATE houseplant_care SET plant_id = :keep WHERE plant_id = :drop"),
                {"keep": keep.id, "drop": drop.id})).rowcount or 0
            await db.execute(text(
                "UPDATE identifications SET matched_plant_id = :keep WHERE matched_plant_id = :drop"),
                {"keep": keep.id, "drop": drop.id})
            if not keep.photo_url and drop.photo_url:
                await db.execute(text(
                    "UPDATE plants SET photo_url = :url, photo_attribution = :who WHERE id = :id"),
                    {"url": drop.photo_url, "who": drop.photo_attribution, "id": keep.id})
                out["photos_moved"] += 1
            await db.execute(text("DELETE FROM plants WHERE id = :id"), {"id": drop.id})
            await db.commit()
            out["care_moved"] += moved
        elif not keep.photo_url and drop.photo_url:
            out["photos_moved"] += 1

        out["merged"] += 1
        if len(out["examples"]) < 20:
            out["examples"].append({"keep": f"{keep.name} ({keep.name_latin})",
                                    "drop": f"{drop.name} ({drop.name_latin})",
                                    "shots": keep.shots})
        if on_piece:
            await on_piece({"done": number, "total": len(pairs), "keep": keep.name_latin})

    return out
