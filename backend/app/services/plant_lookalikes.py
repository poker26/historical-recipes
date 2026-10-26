"""Растения: опасные двойники из справочника пар, а не от модели.

Модель писала двойника растению так же, как грибам: из 225 двойников 189 стояли
у самих ядовитых растений, «болиголов» был у сотни карточек, в том числе у тех,
что на болиголов не похожи (замер 26.10.2026). Теперь двойник берётся только из
классических пар путаницы средней полосы, как у грибов (``fungi_safety``):

* у съедобного растения ``deadly_twin``, ядовитое растение, похожее на него;
* у ядовитого ``confused_with``, съедобные растения, за которые его принимают,
  в творительном падеже с предлогом.

Пара срабатывает, только если и латынь, и русское имя карточки говорят об этом
растении: в атласе «Собачья петрушка» стоит с латынью петрушки, и правило по одной
латыни дало бы ядовитому кокорышу двойника «кокорыш». Уровень опасности здесь не
меняется.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fungi_safety import _keys, fix_text

# латынь (вид или род) → (что ждём в русском имени, значение)
DEADLY_TWIN = {
    "anthriscus sylvestris": (r"купыр", "болиголов пятнистый"),
    "daucus carota": (r"морков", "болиголов пятнистый"),
    "carum carvi": (r"тмин", "болиголов пятнистый"),
    "petroselinum": (r"^(?!.*собач).*петрушк", "кокорыш (собачья петрушка)"),
    "aegopodium podagraria": (r"сныть", "вех ядовитый"),
    "aegopodium": (r"сныть", "вех ядовитый"),
    "angelica archangelica": (r"дягил|дудник", "вех ядовитый"),
    "archangelica officinalis": (r"дягил|дудник", "вех ядовитый"),
    "allium ursinum": (r"черемш|медвеж", "ландыш майский"),
    "allium victorialis": (r"черемш|победн|колба", "чемерица Лобеля"),
}
CONFUSED_WITH = {
    "conium maculatum": (r"болиголов|омег", "с купырём, дикой морковью и петрушкой"),
    "cicuta virosa": (r"вех|цикут", "со снытью и дягилем"),
    "aethusa cynapium": (r"кокорыш|собачья петрушка", "с петрушкой"),
    "convallaria majalis": (r"ландыш", "с черемшой"),
    "colchicum autumnale": (r"безвременник", "с черемшой"),
    "veratrum lobelianum": (r"чемериц", "с черемшой"),
    "veratrum album": (r"чемериц", "с черемшой"),
    "paris quadrifolia": (r"вороний глаз", "с черникой"),
    "atropa belladonna": (r"красавк|белладонн", "с вишней и черникой"),
}


def _pick(table: dict, name: str | None, latin: str | None) -> str | None:
    species, genus = _keys(latin)
    n = (name or "").lower().replace("ё", "е")
    for key in (species, genus):
        hit = table.get(key) if key else None
        if hit and re.search(hit[0].replace("ё", "е"), n):
            return hit[1]
    return None


def lookalikes(name: str | None, latin: str | None, level: int | None) -> tuple[str | None, str | None]:
    """(deadly_twin, confused_with). У ядовитого растения (уровень 3–4) двойника нет."""
    twin = None if (level or 0) >= 3 else _pick(DEADLY_TWIN, name, latin)
    return twin, _pick(CONFUSED_WITH, name, latin)


def fix_monograph(mono: dict, twin: str | None, confused: str | None) -> dict:
    m = dict(mono)
    if isinstance(m.get("safety"), dict):
        m["safety"] = {**m["safety"], "deadly_twin": twin, "confused_with": confused}
    level = (m.get("safety") or {}).get("level") or 0
    verdict = fix_text(m.get("verdict"), level, level_changed=False)
    if verdict:
        m["verdict"] = verdict
    else:
        m.pop("verdict", None)
    cautions = m.get("cautions")
    if isinstance(cautions, dict) and cautions.get("text"):
        m["cautions"] = {**cautions, "text": fix_text(cautions["text"], level, level_changed=False) or None}
    return m


async def fix_plant_twins(db: AsyncSession, apply: bool = False) -> dict:
    """Ставит растениям двойников из справочника. Возвращает отчёт и прежние значения."""
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.name_latin, p.safety_level, p.deadly_twin, p.confused_with, m.monograph
        FROM plants p LEFT JOIN plant_reader_monograph m ON m.plant_id = p.id
        WHERE p.kingdom = 'растение' AND p.rank IN ('species', 'genus')
          AND (coalesce(p.deadly_twin, '') <> '' OR p.confused_with IS NOT NULL
               OR m.monograph->'safety'->>'deadly_twin' IS NOT NULL
               OR m.monograph->>'verdict' ~* 'двойник|не перепута'
               OR m.monograph->'cautions'->>'text' ~* 'двойник|не перепута'
               OR lower(split_part(p.name_latin, ' ', 1)) = ANY(:genera))
    """), {"genera": sorted({_keys(k)[1] for k in list(DEADLY_TWIN) + list(CONFUSED_WITH)})})).all()
    changed, backup = [], []
    for r in rows:
        twin, confused = lookalikes(r.name, r.name_latin, r.safety_level)
        mono = r.monograph if isinstance(r.monograph, dict) else (json.loads(r.monograph) if r.monograph else None)
        new_mono = fix_monograph(mono, twin, confused) if mono is not None else None
        if twin == r.deadly_twin and confused == r.confused_with and new_mono == mono:
            continue
        backup.append({"id": str(r.id), "deadly_twin": r.deadly_twin, "confused_with": r.confused_with,
                       "monograph": mono})
        changed.append({"id": str(r.id), "name": r.name, "name_latin": r.name_latin,
                        "level": r.safety_level, "twin_from": r.deadly_twin, "twin_to": twin,
                        "confused_with": confused,
                        "verdict_from": (mono or {}).get("verdict"),
                        "verdict_to": (new_mono or {}).get("verdict")})
        if apply:
            await db.execute(text(
                "UPDATE plants SET deadly_twin = :tw, confused_with = :cw WHERE id = :id"),
                {"id": r.id, "tw": twin, "cw": confused})
            if new_mono is not None:
                await db.execute(text(
                    "UPDATE plant_reader_monograph SET monograph = CAST(:m AS jsonb), updated_at = now() "
                    "WHERE plant_id = :id"),
                    {"id": r.id, "m": json.dumps(new_mono, ensure_ascii=False)})
    if apply:
        await db.commit()
    return {"changed": changed, "backup": backup}
