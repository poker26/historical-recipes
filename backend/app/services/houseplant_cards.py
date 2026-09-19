"""Карточка комнатного растения: собирается из слоя ухода, без вызовов модели.

Зачем. Человек снимает горшок, определитель верно называет вид — и приложение
показывает пустоту, потому что карточки в травниках нет и быть не может. Мы
обещали людям определение, и молчать в ответ на удачное определение хуже всего.

Как. Карточка собирается из того, что уже лежит в слое: фраза книги, её
источник и страница. Ничего не сочиняется и не пересказывается моделью — сборка
детерминированная, и её можно перезапускать сколько угодно.

Где она живёт. Карточка кладётся в ``plants`` с происхождением ``houseplant``,
рядом с гербарием, но помеченная: на карточках гербария стоят квесты и витрина
«Сейчас в лесу», и тропические комнатные туда попадать не должны. Готовый текст
кладётся в ``plant_reader_monograph``, откуда его берёт ``?view=field`` — тот
самый ответ, который рисует нынешнее приложение, так что дыра закрывается без
выпуска новой версии клиента.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.houseplant_care import CARE_FIELDS, pick_russian_name
from app.services.inaturalist import resolve_registry_photos

# Порядок разделов: сначала то, чем растение убивают чаще всего.
FIELD_ORDER = ["light", "water", "temperature", "humidity", "placement", "soil",
               "feeding", "repotting", "dormancy", "pruning", "propagation"]

SEASON_RU = {"summer": "летом", "winter": "зимой"}

# Сводка в одну фразу: за что хватается человек, открыв карточку.
LEAD_FIELDS = ["water", "light", "temperature"]


def _voice_line(row) -> str:
    """Фраза книги с пометкой сезона, если книга его назвала."""
    season = SEASON_RU.get(row.season or "")
    text_value = (row.value_text or "").strip()
    if season and season not in text_value.lower():
        return f"{season.capitalize()}: {text_value}"
    return text_value


# Как читателю объясняется тяжесть. Слова книги остаются в карточке рядом,
# это только заголовок предупреждения.
SEVERITY_RU = {
    "irritant": "Сок раздражает кожу и слизистые",
    "toxic": "Растение ядовито при попадании внутрь",
    "dangerous": "Растение опасно для жизни",
}


def build_caution(toxicity: list) -> dict | None:
    """Собирает предупреждение об опасности из записей пособия.

    Показываем самое серьёзное из известного и рядом — что делать. Человек с
    ребёнком и кошкой должен увидеть это раньше, чем советы про полив.
    """
    if not toxicity:
        return None

    order = {"dangerous": 3, "toxic": 2, "irritant": 1, "": 0}
    worst = max(toxicity, key=lambda r: order.get(r.severity or "", 0))
    parts = [p for p in (worst.parts or []) if p]

    # Раскладываем под то, что приложение уже умеет рисовать: отдельной строкой
    # ядовитые части, отдельной симптомы, остальное прозой. Так предупреждение
    # читается без выпуска новой версии клиента.
    lines = [SEVERITY_RU.get(worst.severity or "", "Растение небезопасно") + "."]
    if worst.children:
        lines.append("Дети: " + worst.children)
    if worst.pets:
        lines.append("Животные: " + worst.pets)
    if worst.first_aid:
        lines.append("Первая помощь: " + worst.first_aid)

    return {
        "text": " ".join(lines),
        "toxic_parts": parts,
        "symptoms": worst.symptoms or None,
        "severity": worst.severity or "",
        "source": worst.book,
        "page": worst.page,
    }


def build_monograph(latin: str, rows, photo: dict | None, toxicity: list | None = None) -> dict:
    """Собирает монограф карточки из строк слоя ухода.

    Каждый раздел несёт фразы книг как есть. Если книги расходятся, обе фразы
    стоят рядом: усреднять чужие слова мы не беремся.
    """
    by_field: dict[str, list] = {}
    for row in rows:
        by_field.setdefault(row.field, []).append(row)

    sections: list[str] = []
    for field_name in FIELD_ORDER:
        voices = by_field.get(field_name)
        if not voices:
            continue
        lines = []
        seen: set[str] = set()
        for voice in voices:
            line = _voice_line(voice)
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
        if lines:
            sections.append(f"{CARE_FIELDS.get(field_name, field_name)}. " + " ".join(lines))

    # Ведущая мысль — полив, свет или температура: с этого начинают, когда
    # растение уже стоит дома и надо понять, что с ним делать сегодня.
    lead = ""
    for field_name in LEAD_FIELDS:
        voices = by_field.get(field_name)
        if voices:
            lead = _voice_line(voices[0])
            break

    names_ru = [r.taxon_ru for r in rows if r.taxon_ru]
    name_ru = pick_russian_name(names_ru)
    books = sorted({r.book for r in rows if r.book})
    genus_only = " " not in latin

    verdict = ("Комнатное растение. Здесь собрано то, что о нём пишут книги по "
               "комнатному цветоводству: свет, полив, земля, пересадка и размножение.")
    if genus_only:
        verdict += " Книги описывают весь род, поэтому совет годится и для сортов."

    monograph = {
        "name": name_ru or latin,
        "name_latin": latin,
        "kingdom": "растение",
        "origin": "houseplant",
        "verdict": verdict,
        "description": " ".join(sections),
        "care_sections": sections,
        "sources": books,
        "uses_total": 0,
        "recipes": [],
        "recipes_total": 0,
        "compounds": [],
        "compounds_total": 0,
        "culinary": [],
        "parts_used": [],
        "is_toxic": False,
        "reviewed": True,
    }
    caution = build_caution(toxicity or [])
    if caution:
        monograph["cautions"] = caution
        monograph["is_toxic"] = True
        # Предупреждение важнее совета про полив, поэтому в вердикт оно идёт
        # первой фразой: его человек читает раньше всего.
        monograph["verdict"] = f"{SEVERITY_RU.get(caution['severity'], 'Растение небезопасно')}. " + verdict

    if lead:
        monograph["lead_fact"] = {"text": lead, "source": (rows[0].book if rows else None)}
    if photo:
        monograph["photo_url"] = photo.get("photo_url")
        monograph["photo_attribution"] = photo.get("photo_attribution")
        monograph["photo_source"] = "inaturalist"
    return monograph


async def upsert_card(db: AsyncSession, latin: str, rows, photo: dict | None,
                      toxicity: list | None = None) -> tuple[uuid.UUID, bool]:
    """Заводит или обновляет карточку комнатного растения. Возвращает её и признак новизны.

    Если такая латынь уже есть в гербарии, новую карточку не заводим: у
    растения не может быть двух карточек, а травник старше.
    """
    # Ищем ТОЧНОЕ совпадение имени. Раньше поиск захватывал и виды этого рода, и
    # родовая запись слоя «Alocasia» схлопывалась на гербарную карточку
    # «Alocasia macrorhiza»: уход не записывался никуда, а алоказия — один из
    # самых частых снимков.
    existing = (await db.execute(text("""
        SELECT id, origin FROM plants WHERE name_latin ILIKE :exact
        ORDER BY (origin = 'houseplant') DESC LIMIT 1
    """), {"exact": latin})).first()

    names_ru = [r.taxon_ru for r in rows if r.taxon_ru]
    name_ru = pick_russian_name(names_ru) or latin

    if existing and existing[1] != "houseplant":
        return existing[0], False           # карточка гербария — не трогаем

    if existing:
        plant_id = existing[0]
        await db.execute(text("""
            UPDATE plants SET name = :name, photo_url = :photo,
                              photo_attribution = :attribution,
                              is_toxic = is_toxic OR :toxic
            WHERE id = :id
        """), {"id": plant_id, "name": name_ru, "toxic": bool(toxicity),
               "photo": (photo or {}).get("photo_url"),
               "attribution": (photo or {}).get("photo_attribution")})
    else:
        plant_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO plants (id, name, name_latin, kingdom, rank, origin,
                                photo_url, photo_attribution, is_toxic)
            VALUES (:id, :name, :latin, 'растение', :rank, 'houseplant',
                    :photo, :attribution, :toxic)
        """), {"id": plant_id, "name": name_ru, "latin": latin, "toxic": bool(toxicity),
               "rank": "genus" if " " not in latin else "species",
               "photo": (photo or {}).get("photo_url"),
               "attribution": (photo or {}).get("photo_attribution")})

    monograph = build_monograph(latin, rows, photo, toxicity)
    await db.execute(text("""
        INSERT INTO plant_reader_monograph (plant_id, monograph, reviewed, generated_from_hash)
        VALUES (:id, CAST(:mono AS jsonb), true, :hash)
        ON CONFLICT (plant_id) DO UPDATE
            SET monograph = EXCLUDED.monograph, reviewed = true,
                generated_from_hash = EXCLUDED.generated_from_hash
    """), {"id": plant_id,
           "mono": json.dumps(monograph, ensure_ascii=False),
           "hash": f"houseplant:{len(rows)}"})
    return plant_id, existing is None


async def build_cards(db: AsyncSession, limit: int = 0, min_facts: int = 3) -> dict:
    """Собирает карточки для всех растений слоя, о которых книги сказали достаточно.

    ``min_facts`` отсекает растения с одним-двумя случайными утверждениями:
    карточка из одной фразы хуже честного «пока не знаем».
    """
    rows = (await db.execute(text("""
        SELECT c.taxon_latin, c.taxon_ru, c.field, c.season, c.value_text,
               c.source_id, s.title AS book
        FROM houseplant_care c
        JOIN houseplant_source s ON s.id = c.source_id
        WHERE c.latin_verified = true AND c.greenhouse = false
        ORDER BY c.taxon_latin, c.field, c.season NULLS FIRST, s.year DESC NULLS LAST
    """))).all()

    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.taxon_latin, []).append(row)

    # Опасность берём из отдельного источника — пособия по ядовитым комнатным.
    # Ключ тот же, что у ухода: имя вида или его рода.
    danger_rows = (await db.execute(text("""
        SELECT t.taxon_latin, t.severity, t.parts, t.symptoms, t.first_aid,
               t.children, t.pets, t.page, s.title AS book
        FROM houseplant_toxicity t
        JOIN houseplant_source s ON s.id = t.source_id
    """))).all()
    danger: dict[str, list] = {}
    for row in danger_rows:
        danger.setdefault(row.taxon_latin, []).append(row)
        genus = row.taxon_latin.split(" ")[0]
        if genus != row.taxon_latin:
            # Опасность рода относится ко всем его видам: у ароидных она общая.
            danger.setdefault(genus, []).append(row)

    names = [n for n, rs in grouped.items() if len(rs) >= min_facts]
    if limit:
        names = names[:limit]

    photos = await resolve_registry_photos(db, names)

    created = updated = skipped = 0
    for name in names:
        genus = name.split(" ")[0]
        toxicity = danger.get(name) or danger.get(genus) or []
        _plant_id, is_new = await upsert_card(db, name, grouped[name],
                                              photos.get(name), toxicity)
        if is_new:
            created += 1
        else:
            updated += 1
    await db.commit()

    skipped = len(grouped) - len(names)
    with_danger = sum(1 for n in names
                      if danger.get(n) or danger.get(n.split(" ")[0]))
    return {"plants_in_layer": len(grouped), "cards": len(names),
            "created": created, "updated": updated,
            "skipped_thin": skipped, "with_photo": len(photos),
            "with_danger": with_danger}


async def cards_for_latins(db: AsyncSession, latins: list[str]) -> dict[str, dict]:
    """Ищет карточку комнатного растения для каждого определённого вида.

    Определитель называет вид («Syngonium podophyllum»), а книги описывают род
    («Сингониум»), и карточка заведена на род. Без этого поиска связь не
    срабатывала как раз для самых частых съёмок: человек снимал сингониум, а мы
    отвечали, что карточки нет, хотя она есть.

    Возвращает ``{запрошенная латынь: {id, name, name_latin, photo_url, scope}}``,
    где ``scope`` говорит, чья это карточка — вида или его рода.
    """
    wanted: dict[str, tuple[str, str]] = {}
    for latin in latins:
        parts = [p for p in (latin or "").split() if p]
        if not parts:
            continue
        species = " ".join(parts[:2])
        wanted[latin] = (species, parts[0])
    if not wanted:
        return {}

    names = sorted({n for pair in wanted.values() for n in pair})
    # Ищем среди всех карточек, о которых что-то знает слой ухода, а не только
    # среди заведённых нами. У диффенбахии карточка старая, гербарная, и
    # отбрасывать её значило бы отвечать «карточки нет» при живой карточке.
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.name_latin, p.photo_url, p.photo_attribution, p.is_toxic
        FROM plants p
        WHERE p.name_latin = ANY(:names)
          AND (p.origin = 'houseplant'
               OR EXISTS (SELECT 1 FROM houseplant_care c
                          WHERE c.latin_verified AND c.taxon_latin = p.name_latin))
    """), {"names": names})).all()
    known = {r.name_latin: r for r in rows}

    out: dict[str, dict] = {}
    for latin, (species, genus) in wanted.items():
        for name, scope in ((species, "species"), (genus, "genus")):
            row = known.get(name)
            if row:
                out[latin] = {"id": str(row.id), "name": row.name,
                              "name_latin": row.name_latin,
                              "photo_url": row.photo_url,
                              "photo_attribution": row.photo_attribution,
                              "is_toxic": row.is_toxic, "scope": scope}
                break
    return out
