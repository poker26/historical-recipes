"""Biotope domain — canonicalize free-text plant_habitats.biotope into a small
controlled, field-useful vocabulary (multi-label). Mirrors the action/compound
vocab-normalization pattern. Feeds: structured habitat in the reader monograph,
biotope browse/filter, and (with OSM landcover) biotope-aware quest walks.

Validated on real data (pilot 2026-06-17): generic «лес» → the generic tag (NOT
all subtypes); pure place-name lists → []; arid CA-flora gets desert tags.
"""
from app.services.llm import chat_completion_json

# 18 canonical biotopes. Forest is 2-tier: generic «лес» when the source doesn't
# say which, the subtype when it does. Display grouping lives in BIOTOPE_GROUP.
BIOTOPES = [
    "лес", "лес лиственный", "лес хвойный", "лес смешанный",
    "опушки/поляны/вырубки/редколесье",
    "луг", "степь", "поле/сорное",
    "болото/сырое", "берега водоёмов", "водное/прибрежное",
    "каменистые/скалистые склоны", "пески/дюны/обнажения",
    "пустыня/полупустыня", "солончаки/засоленное",
    "горы/предгорья", "сады/парки", "кустарники/заросли",
]
_BSET = set(BIOTOPES)

BIOTOPE_GROUP = {
    **{b: "лес" for b in ("лес", "лес лиственный", "лес хвойный", "лес смешанный",
                          "опушки/поляны/вырубки/редколесье")},
    **{b: "открытое" for b in ("луг", "степь", "поле/сорное")},
    **{b: "влажное" for b in ("болото/сырое", "берега водоёмов", "водное/прибрежное")},
    **{b: "субстрат" for b in ("каменистые/скалистые склоны", "пески/дюны/обнажения",
                               "пустыня/полупустыня", "солончаки/засоленное")},
    "горы/предгорья": "рельеф", "сады/парки": "антропогенное", "кустарники/заросли": "прочее",
}

_SYS = (
    "Ты — фито-эколог. По описанию местообитания растения возвращаешь СТРОГО валидный "
    "JSON со списком подходящих биотопов ТОЛЬКО из заданного словаря (мульти-лейбл; "
    "ничего вне словаря).\n"
    "ПРАВИЛА: (1) регион/страну/названия мест ИГНОРИРУЙ — это не биотоп; если в описании "
    "ТОЛЬКО названия мест/населённых пунктов — верни []. (2) Лес: если указан ТИП "
    "(лиственный/хвойный/смешанный) — бери конкретный подтип; если просто «лес/леса» без "
    "уточнения — бери общий «лес» (НЕ перечисляй все подтипы)."
)


async def canonicalize(biotope_text: str) -> list[str]:
    """Free-text habitat → sorted list of canonical biotopes (subset of BIOTOPES)."""
    if not biotope_text or len(biotope_text.strip()) < 4:
        return []
    voc = ", ".join(f'"{v}"' for v in BIOTOPES)
    # `/no_think` disables qwen3's reasoning trace — for this tiny classification the
    # <think> block ate the 300-token budget → finish_reason=length → empty content →
    # a 5×[5,15,30,60]s ≈ 110s retry storm PER plant that always re-truncated (and
    # wrongly returned []). No-think + a roomier cap → the call just succeeds fast.
    prompt = (f"/no_think\nСловарь биотопов: [{voc}].\n"
              f"Описание: «{biotope_text[:1500]}»\n"
              'Верни JSON: {"biotopes": ["...", ...]} — только из словаря.')
    try:
        out = await chat_completion_json(
            [{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
            task="lightweight", temperature=0.1, max_tokens=800)
    except Exception:
        return []
    tags = out.get("biotopes") if isinstance(out, dict) else None
    if not isinstance(tags, list):
        return []
    return sorted({t for t in tags if t in _BSET})
