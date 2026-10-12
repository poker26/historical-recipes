"""Общие квесты «Подоконник» и «Природа» (docs/RFC-collective-quests.md).

Квест собирают все пользователи вместе. Позиция задаёт род, а не вид: прогон на реальных
снимках показал, что по видам квест застревает на последних редких позициях, а по родам с
заменой застрявших собирается примерно за неделю. Зачёт автоматический: любой снимок, на
котором определение нашло карточку атласа, закрывает открытую позицию её рода.

Замена застрявших позиций и открытие нового квеста идут лениво, при запросе состояния и при
зачёте, под транзакционной блокировкой, поэтому отдельное расписание не нужно.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Настройки. Стартовые числа из прогона на снимках сентября–октября 2026.
SIZE = 15                 # позиций в квесте
REPLACE_DAYS = 3          # позиция без находки дольше этого срока заменяется
POOL_DAYS = 14            # пул родов берётся из снимков за это окно
POOL_TOP_FACTOR = 2       # новый квест берёт SIZE случайных родов из SIZE*2 самых частых
MIN_SIZE = 8              # меньше родов в пуле, чем столько, квест не открывается
FIRST_POINTS = 1          # цена первой закрытой позиции
LAST_POINTS = 5           # цена предпоследней; цена растёт ровно между ними
FINAL_BONUS = 10          # последняя позиция стоит LAST_POINTS + FINAL_BONUS

KINDS = ("home", "nature")
# Не «Подоконник»: так уже называется личный значок за число комнатных видов (meta:windowsill).
TITLES = {"home": "Общий подоконник", "nature": "Общий лес"}
# Фото участника показываем только на улице: снимок с подоконника раскрывает квартиру.
SHOWS_USER_PHOTO = {"home": False, "nature": True}

# Прилагательное по суффиксу перед окончанием, как в чистке исторических названий: голое
# «-ой»/«-ий» бывает у существительных («зверобой», «цикорий»), такие слова не берутся.
_ADJ = re.compile(r"^[а-яё-]*(н|ск|цк|ов|ев|ист|ват|чат|ат|ит|ин|ш|ч|щ|ж|к|г|х|л)(ый|ий|ой|ая|яя|ое|ее|ые|ие)$")
_CYR = re.compile(r"[а-яё]", re.I)
# Названия карточек из травников, которые подписью рода быть не могут: «Гераниевое эфирное масло».
_JUNK_NAME = re.compile(r"масл|эфирн|\bсок\b|настой|отвар", re.I)

# Слоя ухода не хватает, чтобы разделить роды между квестами. Prunus попадает в него через
# лавровишню, хотя снимают сливу и персик в саду; адениума и пахиры в слое нет совсем.
FORCE_HOME = {"Adenium", "Pachira", "Sansevieria", "Dracaena", "Zamioculcas", "Schlumbergera"}
FORCE_NATURE = {"Prunus", "Malus", "Pyrus", "Rubus", "Ribes", "Vaccinium", "Fragaria"}
# Огородные культуры ни в один общий квест не идут: «Общий лес» не про грядку.
EXCLUDE = {"Cucumis", "Cucurbita", "Brassica", "Capsicum", "Lycopersicon", "Phaseolus", "Zea",
           "Pisum", "Beta", "Raphanus"}


def slot_points(order: int, size: int) -> int:
    """Цена позиции, закрытой order-й по счёту из size. Первая стоит FIRST_POINTS, дальше
    цена растёт ровно до LAST_POINTS у предпоследней, последняя добавляет FINAL_BONUS."""
    if order >= size:
        return LAST_POINTS + FINAL_BONUS
    if size <= 2:
        return FIRST_POINTS
    step = (LAST_POINTS - FIRST_POINTS) * (order - 1) / (size - 2)
    return FIRST_POINTS + round(step)


def genus_of(latin: str | None) -> str | None:
    """Латинский род из названия карточки: «Hoya kerrii Craib» → «Hoya»."""
    if not latin:
        return None
    word = latin.strip().split()[0] if latin.strip() else ""
    word = word.strip("×()[],.")
    if not word.isalpha() or not word[0].isalpha() or len(word) < 3:
        return None
    return word[0].upper() + word[1:].lower()


def display_name(species: list[tuple[str, int]]) -> str | None:
    """Подпись позиции по русским названиям снятых видов рода, [(название, снимков)].

    Если большинство снимков у названий, начинающихся с существительного («Хойя керри»,
    «Подберёзовик обыкновенный»), подписью становится это слово. Иначе («Белый гриб»)
    берётся название самого частого вида. Карточки родов для подписи не годятся: у Salix
    их три («Тальник», «Ива», «Верба»), у Hoya устаревший «Плющ восковой»."""
    species = [(n.strip(), c) for n, c in species if n and _CYR.search(n) and not _JUNK_NAME.search(n)]
    if not species:
        return None
    heads: Counter[str] = Counter()
    total = 0
    for name, cnt in species:
        total += cnt
        first = name.split()[0]
        if not _ADJ.match(first.lower()):
            heads[first[0].upper() + first[1:]] += cnt
    if heads:
        head, cnt = heads.most_common(1)[0]
        if cnt * 2 >= total:
            return head
    top = max(species, key=lambda x: x[1])[0]
    return top[0].upper() + top[1:]


# ---- пул родов -----------------------------------------------------------------

_home_cache: tuple[float, set[str], dict[str, str]] = (0.0, set(), {})


def care_genus_name(names: list[str]) -> str | None:
    """Подпись рода из названий слоя ухода за комнатными: «Алоэ, столетник» → «Алоэ»,
    «М. Миля» пропускается как сокращение, «Молочаи» и «Молочай» сводятся к единственному
    числу. Берётся самое частое существительное в начале названия."""
    heads: Counter[str] = Counter()
    for raw in names:
        for part in re.split(r",|\bили\b|[()]", raw or "", flags=re.I):
            words = part.strip().split()
            if not words or not _CYR.search(words[0]) or words[0].endswith("."):
                continue
            w = words[0].strip("()").lower()
            if len(w) < 3 or w in ("или", "род", "вид", "сорт") or _ADJ.match(w):
                continue
            heads[w] += 1
    if not heads:
        return None
    for w in list(heads):                      # «молочаи» → «молочай», «цитрусы» → «цитрус»
        for sing in (w[:-1], w[:-1] + "й"):
            if w.endswith(("ы", "и")) and sing in heads and sing != w:
                heads[sing] += heads.pop(w)
                break
    w = heads.most_common(1)[0][0]
    return w[0].upper() + w[1:]


async def _home_layer(db: AsyncSession) -> tuple[set[str], dict[str, str]]:
    """Роды «Подоконника» и их подписи из слоя ухода. Кэш на час."""
    global _home_cache
    at, genera, names = _home_cache
    if genera and time.monotonic() - at < 3600:
        return genera, names
    rows = (await db.execute(text("""
        SELECT taxon_latin, taxon_ru FROM houseplant_care WHERE latin_verified
        UNION ALL SELECT name_latin, NULL FROM plants WHERE origin = 'houseplant'"""))).all()
    genera, raw = set(), {}
    for latin, ru in rows:
        g = genus_of(latin)
        if not g:
            continue
        genera.add(g)
        if ru:
            raw.setdefault(g, []).append(ru)
    names = {g: n for g, n in ((g, care_genus_name(v)) for g, v in raw.items()) if n}
    genera = (genera | FORCE_HOME) - FORCE_NATURE
    _home_cache = (time.monotonic(), genera, names)
    return genera, names


async def home_genera(db: AsyncSession) -> set[str]:
    """Роды «Подоконника»: слой ухода за комнатными, карточки houseplant и ручные исключения."""
    return (await _home_layer(db))[0]


async def genus_pool(db: AsyncSession, kind: str) -> list[dict]:
    """Роды, снятые за POOL_DAYS, по убыванию числа разных устройств. У каждого рода
    подпись, карточка самого частого вида и её фото."""
    rows = (await db.execute(text("""
        SELECT p.id, p.name_latin, COALESCE(NULLIF(p.name_modern, ''), p.name) AS name, p.photo_url,
               count(*) AS shots, count(DISTINCT i.device_key) AS devices
        FROM identifications i JOIN plants p ON p.id = i.matched_plant_id
        WHERE i.created_at > now() - make_interval(days => :days)
        GROUP BY p.id, p.name_latin, p.name_modern, p.name, p.photo_url"""),
        {"days": POOL_DAYS})).all()
    home, care_names = await _home_layer(db)
    by_genus: dict[str, dict] = {}
    for pid, latin, name, photo, shots, devices in rows:
        g = genus_of(latin)
        if not g or g in EXCLUDE or (g in home) != (kind == "home"):
            continue
        e = by_genus.setdefault(g, {"genus": g, "devices": 0, "species": []})
        e["devices"] += devices        # сумма по видам: устройство с двумя видами рода считается дважды, для порядка это неважно
        e["species"].append({"id": pid, "name": name, "photo": photo, "shots": shots})
    pool = []
    for g, e in by_genus.items():
        # У комнатных подпись из слоя ухода: названия карточек там бывают из травников
        # («Сабур» у алоэ, «Ификуун» у молочая).
        name = (care_names.get(g) if kind == "home" else None) or             display_name([(s["name"], s["shots"]) for s in e["species"]])
        if not name:
            continue
        with_photo = [s for s in e["species"] if s["photo"]] or e["species"]
        top = max(with_photo, key=lambda s: s["shots"])
        pool.append({"genus": g, "name_ru": name, "plant_id": top["id"], "photo_url": top["photo"],
                     "devices": e["devices"]})
    pool.sort(key=lambda x: (-x["devices"], x["genus"]))
    return pool


# ---- открытие квеста и замена позиций ----------------------------------------------

async def _lock(db: AsyncSession) -> None:
    """Одна блокировка на все изменения общих квестов: их немного, а гонка двух зачётов
    за одну позицию или двух открытий квеста хуже, чем короткое ожидание."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('collective_quests'))"))


async def _open_quest(db: AsyncSession, kind: str) -> uuid.UUID | None:
    """Открыть следующий квест вида kind. Вызывается под _lock."""
    pool = await genus_pool(db, kind)
    if len(pool) < MIN_SIZE:
        logger.info("collective %s: в пуле %d родов, квест не открыт", kind, len(pool))
        return None
    size = min(SIZE, len(pool))
    pick = random.sample(pool[:max(size * POOL_TOP_FACTOR, size)], size)
    number = (await db.execute(text(
        "SELECT COALESCE(max(number), 0) + 1 FROM collective_quests WHERE kind = :k"),
        {"k": kind})).scalar()
    qid = (await db.execute(text("""
        INSERT INTO collective_quests (kind, number, size) VALUES (:k, :n, :s) RETURNING id"""),
        {"k": kind, "n": number, "s": size})).scalar()
    for pos, g in enumerate(pick, 1):
        await _insert_slot(db, qid, pos, g)
    logger.info("collective %s №%d открыт: %s", kind, number, ", ".join(g["genus"] for g in pick))
    return qid


async def _insert_slot(db, quest_id, position: int, g: dict) -> None:
    await db.execute(text("""
        INSERT INTO collective_slots (quest_id, position, genus, name_ru, plant_id, photo_url)
        VALUES (:q, :pos, :g, :n, :pid, :ph)"""),
        {"q": quest_id, "pos": position, "g": g["genus"], "n": g["name_ru"],
         "pid": g["plant_id"], "ph": g["photo_url"]})


async def _replace_stale(db: AsyncSession, quest_id, kind: str) -> int:
    """Позиции без находки дольше REPLACE_DAYS уходят, на их место встаёт самый частый род
    пула, которого в квесте ещё не было. Вызывается под _lock."""
    stale = (await db.execute(text("""
        SELECT id, position FROM collective_slots
        WHERE quest_id = :q AND status = 'open'
          AND opened_at < now() - make_interval(days => :d)
        ORDER BY position"""), {"q": quest_id, "d": REPLACE_DAYS})).all()
    if not stale:
        return 0
    used = set((await db.execute(text(
        "SELECT genus FROM collective_slots WHERE quest_id = :q"), {"q": quest_id})).scalars().all())
    fresh = [g for g in await genus_pool(db, kind) if g["genus"] not in used]
    n = 0
    for slot_id, pos in stale:
        if not fresh:
            break        # пул исчерпан: позиция остаётся открытой, пока её кто-нибудь не найдёт
        g = fresh.pop(0)
        await db.execute(text("UPDATE collective_slots SET status = 'replaced' WHERE id = :id"),
                         {"id": slot_id})
        await _insert_slot(db, quest_id, pos, g)
        n += 1
    return n


async def maintain(db: AsyncSession) -> None:
    """Открыть недостающие квесты и заменить застрявшие позиции. Коммитит сам."""
    await _lock(db)
    for kind in KINDS:
        row = (await db.execute(text(
            "SELECT id FROM collective_quests WHERE kind = :k AND status = 'open'"),
            {"k": kind})).first()
        if row is None:
            await _open_quest(db, kind)
        else:
            await _replace_stale(db, row[0], kind)
    await db.commit()


# ---- зачёт снимка ----------------------------------------------------------------

async def credit_shot(db: AsyncSession, device_key: str | None, identification_id: str | None,
                      matched_plant_id: str | None) -> dict | None:
    """Засчитать снимок в общий квест. Возвращает описание для экрана результата, когда
    снимок закрыл позицию, иначе None. Никогда не бросает: квест не должен ломать определение."""
    from app.config import settings
    if not settings.collective_quests_enabled:
        return None
    try:
        dk = uuid.UUID(str(device_key)) if device_key else None
        if dk is None or not matched_plant_id:
            return None
        latin = (await db.execute(text("SELECT name_latin FROM plants WHERE id = :id"),
                                  {"id": matched_plant_id})).scalar()
        genus = genus_of(latin)
        if not genus:
            return None
        blocked = (await db.execute(text(
            "SELECT blocked FROM quest_devices WHERE device_key = :dk"), {"dk": dk})).scalar()
        if blocked:
            return None
        await _lock(db)
        slot = (await db.execute(text("""
            SELECT s.id, s.status, s.name_ru, q.id, q.kind, q.number, q.size
            FROM collective_slots s JOIN collective_quests q ON q.id = s.quest_id
            WHERE q.status = 'open' AND s.genus = :g AND s.status IN ('open', 'closed')"""),
            {"g": genus})).first()
        if slot is None:
            await db.rollback()
            return None
        slot_id, status, name_ru, qid, kind, number, size = slot
        ident = uuid.UUID(identification_id) if identification_id else None
        await db.execute(text("""
            INSERT INTO collective_finds (slot_id, device_key, identification_id)
            VALUES (:s, :dk, :i) ON CONFLICT DO NOTHING"""), {"s": slot_id, "dk": dk, "i": ident})
        if status != "open":
            await db.commit()
            return None
        order = (await db.execute(text("""
            SELECT count(*) + 1 FROM collective_slots WHERE quest_id = :q AND status = 'closed'"""),
            {"q": qid})).scalar()
        pts = slot_points(order, size)
        await db.execute(text("""
            UPDATE collective_slots SET status = 'closed', closed_at = now(), closed_by = :dk,
                   identification_id = :i, close_order = :o, points = :p
            WHERE id = :s"""), {"s": slot_id, "dk": dk, "i": ident, "o": order, "p": pts})
        completed = order >= size
        if completed:
            await _complete(db, qid, kind)
        await db.commit()
        if completed:
            asyncio.create_task(_notify_completed(str(qid)))
        return {"kind": kind, "title": TITLES[kind], "number": number, "slot": name_ru,
                "points": pts, "closed": order, "size": size, "completed": completed}
    except Exception as e:
        logger.warning(f"collective credit skipped: {type(e).__name__}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def _complete(db: AsyncSession, quest_id, kind: str) -> None:
    """Квест собран: итог участникам и сразу следующий квест. Вызывается под _lock."""
    await db.execute(text("""
        UPDATE collective_quests SET status = 'closed', closed_at = now() WHERE id = :q"""),
        {"q": quest_id})
    await db.execute(text("""
        INSERT INTO collective_awards (quest_id, device_key, points, slots, finisher)
        SELECT quest_id, closed_by, sum(points), count(*), bool_or(close_order = q.size)
        FROM collective_slots s JOIN collective_quests q ON q.id = s.quest_id
        WHERE s.quest_id = :q AND s.status = 'closed' AND s.closed_by IS NOT NULL
        GROUP BY quest_id, closed_by"""), {"q": quest_id})
    await _open_quest(db, kind)


async def _notify_completed(quest_id: str) -> None:
    """Push каждому участнику собранного квеста. В своей сессии, после коммита."""
    try:
        from app.database import async_session
        from app.services.push import send_to_device
        async with async_session() as db:
            q = (await db.execute(text(
                "SELECT kind, number, size FROM collective_quests WHERE id = :q"), {"q": quest_id})).first()
            if not q:
                return
            kind, number, size = q
            rows = (await db.execute(text(
                "SELECT device_key, points FROM collective_awards WHERE quest_id = :q"),
                {"q": quest_id})).all()
            for dk, pts in rows:
                await send_to_device(
                    db, str(dk), f"Квест «{TITLES[kind]}» №{number} собран",
                    f"Вместе нашли {size} растений, твои баллы: {pts}. Уже открыт новый квест.",
                    {"type": "collective", "kind": kind})
    except Exception as e:
        logger.warning(f"collective push failed: {type(e).__name__}: {e}")


# ---- состояние для клиента ---------------------------------------------------------

def _person(device_key, nick, avatar, handle, public) -> dict:
    """Кто закрыл позицию. Без своего ника показывается автоматический, как в рейтинге
    («Полевой Можжевельник #5787»). Кто скрыл активность, показывается как «участник»."""
    from app.services.quests import auto_handle, auto_nick
    if public is False or device_key is None:
        return {"nickname": "участник", "avatar": None, "handle": None}
    return {"nickname": nick or auto_nick(device_key), "avatar": avatar,
            "handle": handle or auto_handle(device_key)}


async def state(db: AsyncSession, device_key: str | None, photo_base: str) -> dict:
    """Оба открытых квеста с позициями, баллы пользователя и последние собранные квесты."""
    await maintain(db)
    dk = None
    try:
        dk = uuid.UUID(str(device_key)) if device_key else None
    except ValueError:
        pass
    quests = (await db.execute(text("""
        SELECT id, kind, number, size, created_at FROM collective_quests
        WHERE status = 'open' ORDER BY kind"""))).all()
    out = []
    for qid, kind, number, size, created_at in quests:
        rows = (await db.execute(text("""
            SELECT s.id, s.position, s.genus, s.name_ru, s.plant_id, s.photo_url, s.status,
                   s.opened_at, s.closed_at, s.close_order, s.points, s.photo_hidden,
                   s.closed_by, d.nickname, d.avatar, d.handle, d.activity_public,
                   (i.photo_key IS NOT NULL) AS has_photo,
                   (SELECT count(*) FROM collective_finds f WHERE f.slot_id = s.id) AS finders
            FROM collective_slots s
            LEFT JOIN quest_devices d ON d.device_key = s.closed_by
            LEFT JOIN identifications i ON i.id = s.identification_id
            WHERE s.quest_id = :q AND s.status <> 'replaced'
            ORDER BY s.position"""), {"q": qid})).all()
        slots, mine, closed = [], 0, 0
        for (sid, pos, genus, name, pid, photo, status, opened_at, closed_at, order, pts, hidden,
             closed_by, nick, avatar, handle, public, has_photo, finders) in rows:
            item = {"id": str(sid), "position": pos, "genus": genus, "name": name,
                    "plant_id": str(pid) if pid else None, "photo_url": photo, "status": status,
                    "opened_at": opened_at.isoformat(), "points_now": None}
            if status == "closed":
                closed += 1
                is_me = dk is not None and closed_by == dk
                if is_me:
                    mine += pts or 0
                item.update({
                    "closed_at": closed_at.isoformat(), "order": order, "points": pts,
                    "closed_by": _person(closed_by, nick, avatar, handle, public), "is_me": is_me,
                    "more_finders": max(int(finders) - 1, 0),
                    "user_photo_url": (f"{photo_base}/{sid}.jpg"
                                       if SHOWS_USER_PHOTO[kind] and has_photo and not hidden else None),
                })
            slots.append(item)
        # Сколько стоит позиция, если закрыть её прямо сейчас: следующая по счёту.
        now_pts = slot_points(closed + 1, size)
        for item in slots:
            if item["status"] == "open":
                item["points_now"] = now_pts
                item["replaced_after_days"] = REPLACE_DAYS
        out.append({"id": str(qid), "kind": kind, "title": TITLES[kind], "number": number,
                    "size": size, "closed": closed, "created_at": created_at.isoformat(),
                    "my_points": mine, "shows_user_photo": SHOWS_USER_PHOTO[kind], "slots": slots})

    history = []
    for qid, kind, number, size, closed_at in (await db.execute(text("""
            SELECT id, kind, number, size, closed_at FROM collective_quests
            WHERE status = 'closed' ORDER BY closed_at DESC LIMIT 6"""))).all():
        aw = (await db.execute(text("""
            SELECT a.device_key, a.points, a.finisher, d.nickname, d.avatar, d.handle,
                   d.activity_public
            FROM collective_awards a LEFT JOIN quest_devices d ON d.device_key = a.device_key
            WHERE a.quest_id = :q ORDER BY a.points DESC"""), {"q": qid})).all()
        fin = next((a for a in aw if a[2]), None)
        history.append({
            "kind": kind, "title": TITLES[kind], "number": number, "size": size,
            "closed_at": closed_at.isoformat() if closed_at else None,
            "participants": len(aw),
            "finisher": _person(fin[0], *fin[3:7]) if fin else None,
            "my_points": next((a[1] for a in aw if dk is not None and a[0] == dk), 0),
        })
    total = 0
    if dk is not None:
        total = (await db.execute(text(
            "SELECT COALESCE(sum(points), 0) FROM collective_awards WHERE device_key = :dk"),
            {"dk": dk})).scalar() or 0
    return {"quests": out, "history": history, "my_total_points": int(total),
            "rules": {"size": SIZE, "replace_days": REPLACE_DAYS, "first_points": FIRST_POINTS,
                      "last_points": LAST_POINTS, "final_bonus": FINAL_BONUS}}


async def user_photo(db: AsyncSession, slot_id: str) -> str | None:
    """Ключ снимка в архиве для фото участника, если его можно показывать."""
    row = (await db.execute(text("""
        SELECT i.photo_key FROM collective_slots s
        JOIN collective_quests q ON q.id = s.quest_id
        JOIN identifications i ON i.id = s.identification_id
        WHERE s.id = :s AND s.status = 'closed' AND NOT s.photo_hidden AND q.kind = 'nature'"""),
        {"s": slot_id})).first()
    return row[0] if row else None


async def report_photo(db: AsyncSession, slot_id: str, reporter: str) -> dict:
    """Жалоба на фото: фото скрывается сразу, вернуть его можно в модерации."""
    sid, rk = uuid.UUID(slot_id), uuid.UUID(reporter)
    await db.execute(text("""
        INSERT INTO collective_reports (slot_id, reporter) VALUES (:s, :r) ON CONFLICT DO NOTHING"""),
        {"s": sid, "r": rk})
    await db.execute(text("UPDATE collective_slots SET photo_hidden = true WHERE id = :s"), {"s": sid})
    await db.commit()
    return {"ok": True, "hidden": True}
