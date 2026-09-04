"""Витрина базы «Сейчас в лесу» (RFC-v2 §6) — контентный вход в УТП без фотоаппарата.

Сезонная полка на Home: «что сейчас можно встретить рядом и зачем оно». Строится из
nearby-слоя (живой iNat по точке, ранжирование в пользу корпуса) + крючков из
читательских монографов (lead_fact / verdict). Замер 2026-08-24: 90% открывших карточку
идут в рецепты/состав — контент вовлекает, но входа в него, кроме удачного определения,
не было.

Правила качества (§6): без пустых состояний (нет гео → полка не отдаётся, клиент
прячет блок); скучный крючок хуже отсутствия карточки — крючок фильтруется по длине
и мусорным заглушкам.
"""
import logging
import re
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import quests as quests_svc
from app.services.text_tidy import damage as text_damage, tidy

logger = logging.getLogger(__name__)
router = APIRouter()

_HOOK_MIN = 25      # короче — не факт, а обрубок
_HOOK_MAX = 160     # длиннее — не крючок, а абзац; режем по предложению
_BORING = ("полезное растение", "лекарственное растение", "издавна применя",
           "широко использу", "появится позже")
# Рецептурные ИНСТРУКЦИИ — не крючки (фидбек Олега 2026-08-24: «странные тексты» под
# фото — дозировки и обрывки способов приготовления). Известный дефект lead_fact
# (backlog A-HIGH в reader-monograph) — до его починки фильтруем на витрине.
_INSTRUCTION = re.compile(
    r"\d+\s*(г|мл|кап|стакан|ложк|стол\.|чайн\.)|настаива|залить|залей|кипят|"
    r"отвар(ить|ивать)|принимать|процед|смеша(ть|йте)|измельч|приготовл", re.IGNORECASE)


def _pick_hook(mono: dict | None) -> str | None:
    """Одна живая фраза из монографа. Вердикт ПЕРВЫМ (это выверенное краткое резюме),
    lead_fact вторым (у него известная болезнь — инструкции вместо фактов). Скучное,
    рецептурное и обрубки без финальной точки — в мусор."""
    if not isinstance(mono, dict):
        return None
    for cand in (mono.get("verdict"), (mono.get("lead_fact") or {}).get("text")):
        if not cand or not isinstance(cand, str):
            continue
        # Следы OCR: чинимое (пробел перед запятой, мягкий перенос) правим, а
        # растащенное слово или «!» вместо буквы починить нельзя — такую фразу
        # лучше не показывать вовсе. Из-за неё в витрине висело «Согласно
        # W illfo r t , крапива — хорошее средство против весенней усталости ,».
        t = tidy(" ".join(cand.split())) or ""
        if text_damage(t):
            continue
        if len(t) < _HOOK_MIN:
            continue
        low = t.lower()
        if any(b in low for b in _BORING) or _INSTRUCTION.search(t):
            continue
        if len(t) > _HOOK_MAX:
            cut = t[:_HOOK_MAX]
            dot = max(cut.rfind(". "), cut.rfind("! "))
            if dot <= _HOOK_MIN:
                continue          # первое предложение не влезает целиком → не обрубать
            t = cut[:dot + 1]
        if not t.rstrip().endswith((".", "!", "…")):
            continue              # незаконченная фраза хуже отсутствия крючка
        return t.rstrip()
    return None


@router.get("/seasonal")
async def seasonal(lat: float = Query(...), lng: float = Query(...),
                   limit: int = Query(8, ge=3, le=12),
                   db: AsyncSession = Depends(get_db)):
    """5–8 карточек «что сейчас растёт рядом и зачем оно»: фото, имя, крючок из
    монографа, бейджи безопасности, счётчик рецептов. Тап → карточка растения
    (entry_point=showcase)."""
    month = date.today().month
    near = await quests_svc.nearby(db, lat, lng, month=month, limit=limit * 3)
    corpus_items = [it for it in near.get("items", []) if it.get("plant_id")]
    # Сезон — по фенологии ячейки региона, а не только по месяцу съёмки у iNat
    # (RFC всесезонной версии, Phase A): «сфотографирован в этом месяце» ≠
    # «узнаваем сейчас», и мировая гистограмма подменяет весну тёплыми странами.
    from app.services.phenology import split_by_season, cell_of, ALIVE_MIN
    for it in corpus_items:
        it["latin_key"] = quests_svc._latin_key(it.get("latin"))
    corpus_items, _ = await split_by_season(db, corpus_items, month, cell=cell_of(lat, lng))
    if len(corpus_items) < ALIVE_MIN:
        # Режим «что заготавливают»: в лесу сейчас нечего узнавать, но книги знают,
        # что в этом месяце собирают — почки, кору, шишки, ягоды под снегом.
        return await _harvest_shelf(db, month, limit, near.get("biotopes", []))
    ids = [it["plant_id"] for it in corpus_items]
    # монограф-крючки + safety + счётчик пригодных рецептов — одним проходом
    rows = (await db.execute(text("""
        SELECT p.id::text, p.safety_level,
               m.monograph,
               (SELECT count(*) FROM recipe_ingredients ri
                 JOIN recipes r ON r.id = ri.recipe_id AND r.home_doable
                WHERE ri.plant_id = p.id) AS recipes
        FROM plants p
        LEFT JOIN plant_reader_monograph m ON m.plant_id = p.id AND m.reviewed
        WHERE p.id = ANY(cast(:ids AS uuid[]))"""), {"ids": ids})).all()
    meta = {pid: {"safety": lvl, "hook": _pick_hook(mono), "recipes": rec or 0}
            for pid, lvl, mono, rec in rows}
    out = []
    for it in corpus_items:
        m = meta.get(it["plant_id"], {})
        out.append({
            "plant_id": it["plant_id"], "name": it["name"], "latin": it["latin"],
            "photo": it.get("inat_photo"),
            "hook": m.get("hook"),
            "safety_level": m.get("safety"),
            "recipes": m.get("recipes", 0),
            "biotope_match": it.get("biotope_match", False),
        })
    # ранжирование §6: с крючком > с рецептами > остальные корпусные; внутри —
    # порядок nearby (биотоп+частота) сохраняется стабильной сортировкой
    out.sort(key=lambda x: (x["hook"] is None, x["recipes"] == 0))
    return {"items": out[:limit], "biotopes": near.get("biotopes", []),
            "month": month, "mode": "growing", "title": "Сейчас в лесу"}


_MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле",
                "августе", "сентябре", "октябре", "ноябре", "декабре"]


async def _harvest_shelf(db: AsyncSession, month: int, limit: int, biotopes: list) -> dict:
    """Полка «что заготавливают в <месяце>» — из сроков сбора в корпусе.

    Источник — species_phenology.corpus_months (месяцы сбора надземных частей,
    прямые и выведенные из относительных сроков через пик наблюдений) и сама
    запись о заготовке в plant_harvests: часть, срок, способ, книга и год. Крючок
    карточки — цитата о том, как и когда собирать; это те же карточки растений,
    что и летом, только вход другой. Пусто в этом месяце — полка прячется
    (клиент не рисует пустых состояний)."""
    # Виды отбираются по corpus_months (сумма сроков по виду), а запись — по её
    # СОБСТВЕННОМУ сроку: иначе январская полка показывала «цикорий — трава, в
    # период цветения», потому что у того же цикория корень копают зимой.
    from app.services.phenology import season_months, peak_month
    rows = (await db.execute(text("""
        WITH keys AS (
            SELECT latin_key, inat_months FROM species_phenology
            WHERE corpus_months IS NOT NULL AND CAST(:m AS smallint) = ANY(corpus_months))
        SELECT p.id, p.name, p.name_latin, p.photo_url, p.safety_level,
               h.part, h.season, h.method, b.title AS book, b.year, k.inat_months,
               (SELECT count(*) FROM plant_medicinal_uses u WHERE u.plant_id = p.id) AS facts,
               (SELECT count(*) FROM recipe_ingredients ri
                  JOIN recipes r ON r.id = ri.recipe_id AND r.home_doable
                 WHERE ri.plant_id = p.id) AS recipes
        FROM plants p
        JOIN keys k ON lower(split_part(p.name_latin, ' ', 1) || ' ' || split_part(p.name_latin, ' ', 2)) = k.latin_key
        JOIN plant_harvests h ON h.plant_id = p.id AND h.season IS NOT NULL
        LEFT JOIN books b ON b.id = h.source_book_id
        WHERE p.name_latin IS NOT NULL AND p.name_latin !~ '[А-Яа-я]'
          AND (h.part IS NULL OR h.part !~* 'корень|корни|корневищ|клубн|луковиц')
          -- агрономия, а не сбор: «семена высевают под зиму», «выгонку начинают в декабре»
          AND (h.method IS NULL OR h.method !~* 'высева|посев|сеют|выгонк|высажива|обреза')
        ORDER BY facts DESC, p.id, (b.year IS NULL), length(coalesce(h.method, '')) DESC
        LIMIT 1500"""), {"m": month})).all()
    best: dict = {}
    order: list = []
    for r in rows:
        if month not in season_months(r.season, peak_month(r.inat_months)):
            continue
        if r.id not in best:
            best[r.id] = r
            order.append(r.id)
        if len(order) >= limit:
            break
    out = []
    for pid in order:
        r = best[pid]
        when = (r.season or "").strip()
        how = (r.method or "").strip()
        hook = ("Собирают " + (r.part + ", " if r.part else "") + when).strip()
        if how:
            hook += ". " + how[0].upper() + how[1:]
        if r.book:
            hook += f" — {r.book}" + (f", {r.year}" if r.year else "")
        out.append({
            "plant_id": str(r.id), "name": r.name, "latin": r.name_latin,
            "photo": r.photo_url, "hook": hook[:220],
            "safety_level": r.safety_level, "recipes": r.recipes or 0,
            "biotope_match": False,
        })
    return {"items": out, "biotopes": biotopes, "month": month,
            "mode": "harvest", "title": f"Что заготавливают в {_MONTHS_PREP[month - 1]}"}
