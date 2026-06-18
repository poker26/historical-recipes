"""Forager-safety domain (RFC-edible-safety): assign each plant an ordinal
«will it hurt me if I eat it» level, replacing the over-set binary ``is_toxic``.

    L4 ☠️ deadly — small amount can kill (цикута, болиголов, бледная поганка)
    L3 ☢️ dose-dependent / medicinal-toxic — not food (ландыш, чистотел)
    L2 ⚠️ conditionally edible — safe only after processing / unpalatable (орляк)
    L1 ✅ edible — eaten as ordinary food (малина, сныть)
    L0 ❓ unknown — NOT to be read as safe

A deterministic dry-run proved a regex recompute is UNSAFE (it de-flagged real
poisons — спорынья, зигаденус — because their text mentions animals/dose), so the
LEVEL is decided by the LLM reading each toxicity note in context. Two hard rails
keep it safe: a curated DEADLY anchor (latin) forces L4 (the model may never lower
it), and the model is instructed to ESCALATE on doubt.
"""
import re

from app.services.llm import chat_completion_json

# Latin genera/species that are lethal in a small ingested amount → hard L4 floor.
# Deliberately EXCLUDES dose-dependent L3 plants the user placed below deadly
# (Convallaria=ландыш=L3, Chelidonium=чистотел=L3) — anchor is for «can kill from a
# taste», not «dangerous in quantity».
_DEADLY = re.compile(r"\b(" + "|".join([
    "cicuta", "conium", "aconitum", "atropa", "hyoscyamus", "datura", "scopolia",
    "brugmansia", "colchicum", "digitalis", "daphne", "veratrum", "ricinus",
    "nerium", "oenanthe", "aethusa", "taxus", "gloriosa", "strychnos", "nicotiana",
    "cynanchum", "dictamnus",
    # deadly fungi
    "amanita", "galerina", "lepiota", "conocybe", "inocybe", "cortinarius",
    "gyromitra", "paxillus",
]) + r")\b", re.I)


def is_deadly_anchor(name_latin: str | None) -> bool:
    return bool(name_latin and _DEADLY.search(name_latin))


# OCR / spelling variants of the per-part culinary edibility classes → canonical.
EDIBILITY_CANON = {
    "съедобно": "съедобно", "съедобна": "съедобно", "съедобен": "съедобно",
    "sъедобно": "съедобно", "сьедобно": "съедобно", "едальна": "съедобно",
    "пищ.": "съедобно", "пригоден": "съедобно",
    "условно-съедобно": "условно-съедобно", "условно съедобен": "условно-съедобно",
    "малосъедобно": "условно-съедобно",
    "несъедобно": "несъедобно", "несъедобен": "несъедобно",
    "ядовито": "ядовито", "ядовит": "ядовито",
    "кормовое": "несъедобно", "корм": "несъедобно",
}


_SYS = (
    "Ты — токсиколог-эксперт по дикорастущим растениям и грибам. Оцени БЕЗОПАСНОСТЬ "
    "для ЧЕЛОВЕКА, который нашёл растение в лесу и думает его съесть. Отвечай СТРОГО "
    "валидным JSON.\n\n"
    "Уровни (worst-case для человека-едока):\n"
    "4 — СМЕРТЕЛЬНО: даже малое количество может убить (цикута, болиголов, аконит, "
    "белладонна, бледная поганка). Не трогать.\n"
    "3 — ОПАСНО/дозозависимо: малые дозы — лекарство, большие — тяжёлое отравление; "
    "как ЕДУ не употреблять (ландыш, чистотел, наперстянка, чемерица).\n"
    "2 — УСЛОВНО съедобно: безопасно ТОЛЬКО после обработки (вымачивание/варка) или "
    "сильно невкусно (папоротник-орляк, грузди).\n"
    "1 — СЪЕДОБНО: едят как обычную еду без опасности (малина, сныть).\n"
    "0 — НЕТ ДАННЫХ: информации недостаточно для вывода → НЕ считать безопасным.\n\n"
    "ПРАВИЛА:\n"
    "1) Токсичность ТОЛЬКО ДЛЯ ЧЕЛОВЕКА. Ядовитость для птиц/рыб/скота/насекомых/моли "
    "НЕ влияет на уровень (анис ядовит для птиц, но человеку съедобен → 1).\n"
    "2) Один расплывчатый источник («по некоторым данным», «считается ядовитым») без "
    "конкретных симптомов — слабый сигнал; НЕ поднимай до 4 только на нём.\n"
    "3) При СОМНЕНИИ выбирай БОЛЕЕ ОПАСНЫЙ уровень — лучше зря предупредить.\n"
    "4) Учитывай ЧАСТИ: какие съедобны, какие опасны (ревень: черешок съедобен, "
    "лист ядовит).\n"
    "5) `level` — опасность САМОГО растения при ПРАВИЛЬНОМ определении. Опасный "
    "двойник — ОТДЕЛЬНОЕ поле `deadly_twin` и САМ ПО СЕБЕ уровень НЕ повышает: "
    "съедобное растение со смертельным двойником остаётся съедобным по level (1/2), "
    "но с пометкой двойника (купырь съедобен → level 1-2, deadly_twin «болиголов»; "
    "болиголов же сам по себе → level 4).\n\n"
    'Верни JSON: {"level": 0..4, "edible_parts": ["..."], "dangerous_parts": ["..."], '
    '"deadly_twin": "<рус.название или null>", "rationale": "<1-2 фразы по-русски>"}'
)


async def classify_safety(plant: dict) -> dict:
    """LLM forager-safety level for one plant. ``plant`` carries name, name_latin,
    family, edibility classes, toxicity note texts, and has_medicinal. The deadly
    anchor is applied by the CALLER as a floor the model can't undercut."""
    tox = "\n".join(f"- {t}" for t in plant.get("toxicity_texts", []) if t) or "— нет —"
    edi = ", ".join(plant.get("edibility", [])) or "— нет —"
    user = (
        f"Растение: {plant.get('name')}\n"
        f"Латынь: {plant.get('name_latin') or '—'}\n"
        f"Семейство: {plant.get('family') or '—'}\n"
        f"Кулинарная съедобность (по частям, из источников): {edi}\n"
        f"Лекарственное применение в корпусе: {'да' if plant.get('has_medicinal') else 'нет'}\n"
        f"Заметки о токсичности (дословно из источников):\n{tox}\n\n"
        "Оцени уровень безопасности для человека-едока."
    )
    out = await chat_completion_json(
        [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        task="plant_extraction", temperature=0.1, max_tokens=900)
    if not isinstance(out, dict):
        return {"level": 0, "edible_parts": [], "dangerous_parts": [],
                "deadly_twin": None, "rationale": "классификатор не вернул JSON"}
    lvl = out.get("level")
    lvl = lvl if isinstance(lvl, int) and 0 <= lvl <= 4 else 0
    return {
        "level": lvl,
        "edible_parts": out.get("edible_parts") or [],
        "dangerous_parts": out.get("dangerous_parts") or [],
        "deadly_twin": out.get("deadly_twin") or None,
        "rationale": (out.get("rationale") or "")[:600],
    }
