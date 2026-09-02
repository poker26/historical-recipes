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
    if not corpus_items:
        return {"items": [], "biotopes": near.get("biotopes", [])}
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
            "month": month}
