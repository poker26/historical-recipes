"""Ароидные: уровень безопасности, плашка и первая фраза карточки по правилу.

Правило живёт в ``edible_safety.aroid_rule`` и держит уровень при любом будущем
пересчёте. Здесь — то, что уже лежит в базе и в монографах карточек: уровень,
обоснование, плашка безопасности и первая фраза. Модель писала замиокулькасу
«СМЕРТЕЛЬНО ядовито», аглаонеме «цианистые соединения», эпипремнуму «нет данных».

Прогон детерминированный и идемпотентный. Перед записью прежние значения
отдаются вызывающему, чтобы их можно было сохранить и вернуть.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.edible_safety import AROID_VERDICT, aroid_rule

# Фразы первой строки карточки, которые противоречат уровню ароидного растения.
_CONTRADICTS = re.compile(
    r"смертельн|безопасн|недостаточно данных|достоверных данных|нет данных|ядовит|опасно для жизни"
    r"|токсичн|не рекомендуется",
    re.I)
# Служебные фразы прежней сборки карточек комнатных: читателю не нужно объяснять,
# откуда взят текст.
_BOILERPLATE = re.compile(r"^Комнатное растение\.$|Здесь собрано то, что о нём пишут книги|"
                          r"Книги описывают весь род")


def fix_verdict(verdict: str | None, level: int) -> str:
    """Первая фраза без всего, что противоречит уровню или повторяет плашку.

    Об опасности говорит красная плашка над фразой, поэтому сама фраза её не
    повторяет (на экране OnePlus 20.10.2026 стояло дважды «ядовито: сок
    обжигает рот и горло»). Остаётся то, что плашке не противоречит: родина.
    """
    sentences = re.split(r"(?<=[.!?])\s+", (verdict or "").strip())
    keep = [s for s in sentences
            if s and not _CONTRADICTS.search(s) and not _BOILERPLATE.search(s)
            and s not in AROID_VERDICT.values()]
    return " ".join(keep).strip()


def fix_monograph(mono: dict, rule: dict) -> dict:
    m = dict(mono)
    level = rule["level"]
    m["safety"] = {"level": level, "label": rule["label"], "rationale": rule["rationale"],
                   "edible_parts": rule["edible_parts"], "dangerous_parts": rule["dangerous_parts"],
                   "deadly_twin": None}
    m["is_toxic"] = level >= 3
    roles = [r for r in (m.get("roles") or []) if r != "toxic"]
    if level >= 3:
        roles.append("toxic")
    if "roles" in m or level >= 3:
        m["roles"] = roles
    verdict = m.get("verdict") or ""
    low = verdict.lower()
    stale = "смертельн" in low or "безопасн" in low or verdict.startswith(tuple(AROID_VERDICT.values()))
    if level != 2 or stale:
        verdict = fix_verdict(verdict, level)
        if verdict:
            m["verdict"] = verdict
        else:
            m.pop("verdict", None)
    return m


async def fix_aroid_cards(db: AsyncSession, apply: bool = False) -> dict:
    """Приводит карточки ароидных к правилу. Возвращает отчёт и прежние значения."""
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.name_latin, p.safety_level, p.safety_rationale, p.is_toxic,
               p.edible_parts, p.dangerous_parts, p.deadly_twin, m.monograph
        FROM plants p LEFT JOIN plant_reader_monograph m ON m.plant_id = p.id
        WHERE p.rank IN ('species', 'genus') AND p.name_latin IS NOT NULL
    """))).all()
    changed, backup = [], []
    for r in rows:
        rule = aroid_rule(r.name_latin)
        if not rule:
            continue
        mono = r.monograph if isinstance(r.monograph, dict) else (json.loads(r.monograph) if r.monograph else None)
        new_mono = fix_monograph(mono, rule) if mono is not None else None
        if (r.safety_level == rule["level"] and r.safety_rationale == rule["rationale"]
                and new_mono == mono):
            continue
        backup.append({"id": str(r.id), "name_latin": r.name_latin, "safety_level": r.safety_level,
                       "safety_rationale": r.safety_rationale, "is_toxic": r.is_toxic,
                       "edible_parts": r.edible_parts, "dangerous_parts": r.dangerous_parts,
                       "deadly_twin": r.deadly_twin, "monograph": mono})
        changed.append({"name_latin": r.name_latin, "name": r.name, "from": r.safety_level,
                        "to": rule["level"],
                        "verdict_from": (mono or {}).get("verdict"),
                        "verdict_to": (new_mono or {}).get("verdict")})
        if apply:
            await db.execute(text("""
                UPDATE plants SET safety_level = :l, safety_rationale = :why, is_toxic = :tox,
                       edible_parts = :ep, dangerous_parts = :dp, deadly_twin = NULL
                WHERE id = :id"""), {"id": r.id, "l": rule["level"], "why": rule["rationale"],
                                     "tox": rule["level"] >= 3, "ep": rule["edible_parts"],
                                     "dp": rule["dangerous_parts"]})
            if new_mono is not None:
                await db.execute(text(
                    "UPDATE plant_reader_monograph SET monograph = CAST(:m AS jsonb) WHERE plant_id = :id"),
                    {"id": r.id, "m": json.dumps(new_mono, ensure_ascii=False)})
    if apply:
        await db.commit()
    return {"changed": changed, "backup": backup}
