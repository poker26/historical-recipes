"""LLM-adjudication layer for the data-quality linter (RFC-data-quality-llm).

The validators find CANDIDATES; an adjudicator renders a grounded VERDICT on each
(real / false_positive / uncertain) + a suggested action, so thousands of findings
can be triaged at scale instead of by hand. One registered adjudicator per
``check_id`` (a context builder + prompt). Verdicts are cached on the finding row
(``llm_*`` columns) — re-runs skip already-judged findings.

Uses the lightweight model (qwen3-32b) — real/false is an easy call. Grounding is
mandatory: the prompt forces the model to cite the finding's data (names/latin);
thin data → ``uncertain`` (we don't manufacture hallucinations during cleanup).
"""
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from app.config import settings
from app.database import async_session
from app.models.plant import Plant
from app.models.recipe import Recipe, RecipeIngredient
from app.models.data_quality import DataQualityFinding
from app.services.llm import chat_completion_json

_ADJUDICATORS: dict[str, "callable"] = {}


def adjudicator(check_id: str):
    def deco(fn):
        _ADJUDICATORS[check_id] = fn
        return fn
    return deco


def registered_adjudicators() -> list[str]:
    return sorted(_ADJUDICATORS.keys())


# ── alias.collision ───────────────────────────────────────────────────
_ALIAS_SYS = (
    "Ты — куратор ботанической базы данных. Отвечаешь СТРОГО валидным JSON, "
    "опираясь только на приведённые имена и латынь, без домыслов."
)


@adjudicator("alias.collision")
async def _adj_alias_collision(db, f: DataQualityFinding) -> dict:
    ev = f.evidence or {}
    a_name = ev.get("plant", "?")
    a_latin = ev.get("plant_latin") or "нет"
    lines = []
    for col in ev.get("collisions", []):
        alias = col.get("alias")
        for other in col.get("collides_with", []):
            b_latin = (await db.execute(
                select(Plant.name_latin).where(Plant.id == other.get("id")))).scalar()
            lines.append(f'  алиас «{alias}» = основное имя растения «{other.get("name")}» (латынь: {b_latin or "нет"})')
    prompt = (
        f"Растение A: «{a_name}» (латынь: {a_latin}).\n"
        f"В его списке «другие названия» стоят алиасы, совпадающие с ОСНОВНЫМ именем "
        f"ДРУГОГО растения:\n" + "\n".join(lines) + "\n\n"
        "Вопрос: эти алиасы у A присвоены ОШИБОЧНО (мина матчера — это разные виды, "
        "имя принадлежит другому растению, алиас надо УБРАТЬ у A), или это ЛЕГИТИМНОЕ "
        "народное имя, которое оба растения реально делят?\n"
        "Опирайся на латынь и имена (разные роды/виды → почти наверняка ошибка).\n"
        'Верни JSON: {"verdict":"real|false_positive|uncertain","confidence":0.0-1.0,'
        '"strip":["алиас",...],"keep":["алиас",...],"reasoning":"<кратко, со ссылкой на латынь/имена>"}\n'
        '"real" = есть что убрать (strip непустой); "false_positive" = всё легитимно.'
    )
    out = await chat_completion_json(
        [{"role": "system", "content": _ALIAS_SYS}, {"role": "user", "content": prompt}],
        task="lightweight", temperature=0.1, max_tokens=1024,
    )
    if not isinstance(out, dict):
        return {"verdict": "uncertain", "confidence": 0.0, "action": None,
                "reasoning": "LLM returned non-object"}
    strip = [s for s in (out.get("strip") or []) if isinstance(s, str)]
    verdict = out.get("verdict", "uncertain")
    action = "strip_aliases" if (verdict == "real" and strip) else (
        "keep" if verdict == "false_positive" else None)
    res = {"verdict": verdict, "confidence": float(out.get("confidence") or 0.0),
           "action": action, "reasoning": str(out.get("reasoning") or "")[:600]}
    if action == "strip_aliases":
        # refine the existing fix to exactly what the LLM approved
        res["refined_aliases"] = strip
    return res


# ── compound.bad_hierarchy ────────────────────────────────────────────
_HIER_SYS = (
    "Ты — химик-куратор базы веществ. Отвечаешь СТРОГО валидным JSON, опираясь только "
    "на приведённые названия и классы, без домыслов."
)


@adjudicator("compound.bad_hierarchy")
async def _adj_compound_hierarchy(db, f: DataQualityFinding) -> dict:
    ev = f.evidence or {}
    child, parent = ev.get("child", "?"), ev.get("parent", "?")
    cclass = ev.get("child_class") or "—"
    cf, pf = ev.get("child_facts"), ev.get("parent_facts")
    prompt = (
        f"Вещество-РЕБЁНОК: «{child}» (класс: {cclass}; встречается у {cf} растений).\n"
        f"Указано как ПОТОМОК (parent) вещества: «{parent}» (встречается у {pf} растений).\n\n"
        f"Вопрос: «{parent}» — это настоящий химический КЛАСС/группа, к которой «{child}» "
        f"таксономически ПРИНАДЛЕЖИТ (специфика→класс, напр. кверцетин→флавоноиды, "
        f"пектины→полисахариды)? ИЛИ «{parent}» — это конкретное вещество / смесь / "
        f"экссудат (камедь, прополис, маточное молочко, конкретный яд), которое лишь "
        f"СОДЕРЖИТ «{child}» — и тогда это ошибка (containment прочитан как таксономия), "
        f"ребро надо УБРАТЬ?\n"
        f"ГЛАВНОЕ ПРАВИЛО: если «{child}» таксономически ЯВЛЯЕТСЯ частным случаем «{parent}» "
        f"(манноза — это гексоза; амигдалин — цианогенный гликозид; молибден — тяжёлый "
        f"металл; фитостеролы — стеролы; тригонеллин — бетаин), это ПРАВИЛЬНОЕ ребро "
        f"(false_positive) — ДАЖЕ если ребёнок встречается ЧАЩЕ родителя. Containment-ошибка "
        f"(real) — ТОЛЬКО когда «{parent}» это физический МАТЕРИАЛ/смесь/секрет/включение "
        f"(камедь, прополис, маточное молочко, друзы, одеревяневшие клетки, конкретный яд), "
        f"а НЕ химический класс/группа. Сомневаешься, класс это или вещество → uncertain.\n"
        'Верни JSON: {"verdict":"real|false_positive|uncertain","confidence":0.0-1.0,'
        '"reasoning":"<кратко, со ссылкой на названия/классы>"}\n'
        '"real" = это containment-ошибка, ребро убрать; "false_positive" = родитель '
        "настоящий класс-предок, оставить."
    )
    out = await chat_completion_json(
        [{"role": "system", "content": _HIER_SYS}, {"role": "user", "content": prompt}],
        task="lightweight", temperature=0.1, max_tokens=512,
    )
    if not isinstance(out, dict):
        return {"verdict": "uncertain", "confidence": 0.0, "action": None,
                "reasoning": "LLM returned non-object"}
    verdict = out.get("verdict", "uncertain")
    action = "null_compound_parent" if verdict == "real" else (
        "keep" if verdict == "false_positive" else None)
    return {"verdict": verdict, "confidence": float(out.get("confidence") or 0.0),
            "action": action, "reasoning": str(out.get("reasoning") or "")[:600]}


# ── domain.recipe_is_monograph ────────────────────────────────────────
_RECIPE_MONO_SYS = (
    "Ты — куратор базы народных рецептов. Отвечаешь СТРОГО валидным JSON, опираясь "
    "только на приведённый рецепт, без домыслов."
)


@adjudicator("domain.recipe_is_monograph")
async def _adj_recipe_monograph(db, f: DataQualityFinding) -> dict:
    try:
        rid = uuid.UUID(f.entity_id)
    except Exception:
        return {"verdict": "uncertain", "confidence": 0.0, "action": None,
                "reasoning": "bad entity_id"}
    recipe = (await db.execute(select(Recipe).where(Recipe.id == rid))).scalar_one_or_none()
    if recipe is None:
        return {"verdict": "uncertain", "confidence": 0.0, "action": None,
                "reasoning": "recipe gone"}
    ings = [i for i in (await db.execute(
        select(RecipeIngredient.name).where(RecipeIngredient.recipe_id == rid))).scalars().all() if i]
    text = (recipe.original_text or recipe.normalized_text or "")[:600]
    prompt = (
        f"Рецепт: «{recipe.name}» (категория: {recipe.category}).\n"
        f"Ингредиенты ({len(ings)}): {', '.join(ings[:15]) or '—'}.\n"
        f"Текст: {text}\n\n"
        f"Имя рецепта совпадает с названием вида растения. Определи: это МОНОГРАФИЯ-ДАМП — "
        f"экстрактор по ошибке сделал фейковый «сбор» из монограф-секции про ОДНО растение "
        f"(содержимое описывает сам вид «{recipe.name}», его части и применение, а НЕ "
        f"препарат из нескольких РАЗНЫХ растений)?\n"
        f"• is_monograph_dump=true — по сути запись об ОДНОМ растении (один вид-компонент; "
        f"перечисленные «ингредиенты» — это части/формы того же вида; текст = описание "
        f"растения). Такой рецепт УДАЛИМ.\n"
        f"• is_monograph_dump=false — НАСТОЯЩИЙ препарат: ≥2 РАЗНЫХ ВИДА растений в сборе, "
        f"ИЛИ именованный аптечный препарат (Ротокан, Коргликон), ИЛИ явный многокомпонентный "
        f"способ. Такой ОСТАВИМ.\n"
        'Верни JSON: {"is_monograph_dump": true|false, "confidence":0.0-1.0, "reasoning":"<кратко>"}'
    )
    out = await chat_completion_json(
        [{"role": "system", "content": _RECIPE_MONO_SYS}, {"role": "user", "content": prompt}],
        task="lightweight", temperature=0.1, max_tokens=700,
    )
    if not isinstance(out, dict) or "is_monograph_dump" not in out:
        return {"verdict": "uncertain", "confidence": 0.0, "action": None,
                "reasoning": "LLM returned no decision"}
    dump = out.get("is_monograph_dump")
    if dump is True:
        verdict, action = "real", "delete_recipe_and_qdrant"
    elif dump is False:
        verdict, action = "false_positive", "keep"
    else:
        verdict, action = "uncertain", None
    return {"verdict": verdict, "confidence": float(out.get("confidence") or 0.0),
            "action": action, "reasoning": str(out.get("reasoning") or "")[:600]}


# ── runner ────────────────────────────────────────────────────────────
async def run_adjudication(check_id: str, limit: int = 50, concurrency: int = 5) -> dict:
    """Adjudicate up to `limit` not-yet-judged open findings of `check_id`."""
    if check_id not in _ADJUDICATORS:
        return {"error": f"no adjudicator for {check_id}", "available": registered_adjudicators()}
    fn = _ADJUDICATORS[check_id]
    model = settings.llm_model_lightweight

    async with async_session() as db:
        findings = (await db.execute(
            select(DataQualityFinding).where(
                DataQualityFinding.check_id == check_id,
                DataQualityFinding.status == "open",
                DataQualityFinding.llm_verdict.is_(None),
            ).limit(limit)
        )).scalars().all()
        ids = [f.id for f in findings]

    if not ids:
        return {"check_id": check_id, "judged": 0, "remaining": 0}

    sem = asyncio.Semaphore(concurrency)
    now = datetime.now(timezone.utc)
    tally: dict[str, int] = {}

    async def _one(fid):
        async with sem:
            async with async_session() as db:
                f = (await db.execute(select(DataQualityFinding).where(DataQualityFinding.id == fid))).scalar_one()
                try:
                    r = await fn(db, f)
                except Exception as e:
                    r = {"verdict": "uncertain", "confidence": 0.0, "action": None,
                         "reasoning": f"adjudicator error: {str(e)[:120]}"}
                f.llm_verdict = r.get("verdict")
                f.llm_confidence = r.get("confidence")
                f.llm_action = r.get("action")
                f.llm_reasoning = r.get("reasoning")
                f.llm_model = model
                f.llm_at = now
                if r.get("refined_aliases") is not None and isinstance(f.suggested_fix, dict):
                    sf = dict(f.suggested_fix)
                    sf["aliases"] = r["refined_aliases"]
                    f.suggested_fix = sf
                await db.commit()
                tally[r.get("verdict") or "none"] = tally.get(r.get("verdict") or "none", 0) + 1

    await asyncio.gather(*[_one(fid) for fid in ids])

    async with async_session() as db:
        remaining = (await db.execute(
            select(func.count()).select_from(DataQualityFinding).where(
                DataQualityFinding.check_id == check_id,
                DataQualityFinding.status == "open",
                DataQualityFinding.llm_verdict.is_(None),
            ))).scalar() or 0
    return {"check_id": check_id, "judged": len(ids), "remaining": remaining, "verdicts": tally}
