"""LLM-based extraction of plant monographs for the herbalism domain.

Takes the (cleaned/translated) text of a herbal and extracts one structured
``ExtractedPlant`` per plant entry: identity (names + family), botanical
description, medicinal uses, chemical compounds, harvest notes, habitats and
toxicity. Each fact preserves verbatim source text so multiple sources can
enrich the same plant in layers.
"""

import re
from dataclasses import dataclass, field

from app.services.llm import chat_completion_json

# A monograph entry runs ~2–4K chars; several fit in a window. Output is dense
# (structured sub-objects per plant), so keep input modest to stay inside the
# 32K-token response budget for token-dense Cyrillic.
MAX_CHARS_PER_CALL = 14_000

# Plant-entry header, e.g. "Ежевика сизая – Rubus caesius L." — a Russian name,
# a dash, then a Latin binomial (capitalised genus). Used to split the book at
# entry boundaries so a monograph never gets cut in half across LLM calls.
_HEADER_RE = re.compile(r"^.{2,70}?\s[–—-]\s[A-Z][a-zà-ÿ]+\b")

# Light normalization of the plant part to a small controlled set; unknown
# values pass through unchanged (lowercased).
_PART_CANON = {
    "листья": "лист", "лист": "лист", "листы": "лист",
    "корни": "корень", "корень": "корень", "корневище": "корневище", "корневища": "корневище",
    "цветки": "цвет", "цветы": "цвет", "цвет": "цвет", "соцветия": "цвет",
    "плоды": "плод", "плод": "плод", "ягоды": "плод", "ягода": "плод",
    "семена": "семя", "семя": "семя", "семечки": "семя",
    "кора": "кора", "трава": "трава", "побеги": "побег", "почки": "почка",
    "сок": "сок", "клубни": "клубень", "луковица": "луковица",
}


@dataclass
class ExtractedMedicinalUse:
    part: str = ""
    action: str = ""            # action term as written (normalized to action_id later)
    indications: str = ""
    preparation: str = ""
    dosage: str = ""
    contraindications: str = ""
    original_text: str = ""


@dataclass
class ExtractedCompound:
    compound: str = ""
    compound_group: str = ""
    part: str = ""
    notes: str = ""


@dataclass
class ExtractedHarvest:
    part: str = ""
    season: str = ""
    method: str = ""
    original_text: str = ""


@dataclass
class ExtractedHabitat:
    region: str = ""
    biotope: str = ""
    status: str = ""
    original_text: str = ""


@dataclass
class ExtractedToxicity:
    toxic_parts: list[str] = field(default_factory=list)
    symptoms: str = ""
    antidote: str = ""
    severity: str = ""
    original_text: str = ""


@dataclass
class ExtractedPlant:
    name: str
    name_latin: str = ""
    family: str = ""
    family_latin: str = ""
    names_historical: list[str] = field(default_factory=list)
    description: str = ""
    parts_used: list[str] = field(default_factory=list)
    is_toxic: bool = False
    original_text: str = ""
    medicinal_uses: list[ExtractedMedicinalUse] = field(default_factory=list)
    compounds: list[ExtractedCompound] = field(default_factory=list)
    harvests: list[ExtractedHarvest] = field(default_factory=list)
    habitats: list[ExtractedHabitat] = field(default_factory=list)
    toxicities: list[ExtractedToxicity] = field(default_factory=list)


SYSTEM_PROMPT = """You are extracting structured monographs about medicinal plants from a Russian herbal \
(травник / лекарственный справочник). The text describes plants one entry at a time. Each entry typically \
opens with a heading like "Ежевика сизая – Rubus caesius L." and may contain sections such as Семейство, \
Другие названия, a botanical description, Распространение, Химический состав, Применение, Заготовка.

Extract EVERY distinct plant entry you find. For each plant return an object with:
- name: canonical Russian name in nominative case (e.g. "Ежевика сизая")
- name_latin: Latin binomial with author if present (e.g. "Rubus caesius L."), else ""
- family: Russian family name (e.g. "розовые"), else ""
- family_latin: Latin family if present (e.g. "Rosaceae"), else ""
- names_historical: array of other/folk/historical names ("Другие названия"), else []
- description: botanical morphology and phenology (appearance, flowering/fruiting time) as plain text
- parts_used: array of plant parts used medicinally (лист, корень, цвет, плод, семя, кора, трава, сок ...)
- is_toxic: true if the source marks the plant as poisonous/toxic, else false
- medicinal_uses: array; ONE entry per distinct action/use. Each:
    - part: which plant part this use applies to (or "" if unspecified)
    - action: the medicinal action in one phrase (e.g. "мочегонное", "противовоспалительное", "вяжущее")
    - indications: conditions/diseases it is used for (free text)
    - preparation: form of use if stated (настой / отвар / настойка / мазь / припарка / сок / порошок), else ""
    - dosage: dosing or method-of-use detail if stated, else ""
    - contraindications: cautions/contraindications if stated, else ""
    - original_text: the exact source sentence(s) this use came from
- compounds: array of chemical/active constituents (from "Химический состав"). Each:
    - compound: substance name (e.g. "дубильные вещества", "цимарин")
    - compound_group: class if identifiable (алкалоиды / гликозиды / флавоноиды / эфирное масло / сапонины / витамины / органические кислоты ...), else ""
    - part: part it occurs in if stated ("в плодах", "в листьях" -> "плод"/"лист"), else ""
    - notes: ""
- harvests: array of collection/preparation notes (from "Заготовка"). Each:
    - part, season (when to collect), method (how to gather/dry/store), original_text
- habitats: array (from "Распространение"). Each:
    - region (geography), biotope (where it grows), status ("Красная книга", "редкое", ...), original_text
- toxicities: array, only if poisonous. Each:
    - toxic_parts (array), symptoms, antidote, severity, original_text

Rules:
- Preserve original_text fields VERBATIM. Do NOT invent facts not in the text.
- If a section is absent, return an empty array / empty string — never fabricate.
- Do NOT extract table-of-contents lines, chapter intros, or general pharmacology essays that name no specific plant.

Return a JSON object with a single key "plants" whose value is an ARRAY of plant objects: \
{"plants": [ {"name": ...}, {"name": ...} ]}."""


def _coerce_list(val) -> list[dict]:
    """Normalize an LLM JSON response to a list of plant dicts."""
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for key in ("plants", "result", "results", "data"):
            inner = val.get(key)
            if isinstance(inner, list):
                return inner
        if "name" in val:
            return [val]
        for v in val.values():
            if isinstance(v, list):
                return v
    return []


def _canon_part(raw: str) -> str:
    p = (raw or "").strip().lower()
    return _PART_CANON.get(p, p)


def _str(v) -> str:
    return (v if isinstance(v, str) else "" if v is None else str(v)).strip()


def _str_list(v) -> list[str]:
    if isinstance(v, list):
        return [_str(x) for x in v if _str(x)]
    s = _str(v)
    return [s] if s else []


def _split_into_chunks(text: str) -> list[str]:
    """Split a herbal into LLM-sized chunks at plant-entry header boundaries.

    Accumulates lines; once the buffer reaches the target size it flushes only
    when the next line looks like a new entry header, so individual monographs
    stay intact within a single call.
    """
    if len(text) <= MAX_CHARS_PER_CALL:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in text.split("\n"):
        is_header = bool(_HEADER_RE.match(line.strip()))
        if buf and buf_len >= MAX_CHARS_PER_CALL and is_header:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(line)
        buf_len += len(line) + 1
        # Hard cap: if a single entry somehow blows far past the budget, flush
        # anyway so we never build an oversized prompt.
        if buf_len >= MAX_CHARS_PER_CALL * 2:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
    if buf:
        chunks.append("\n".join(buf))
    return chunks


async def extract_plants_from_text(
    text: str,
    book_title: str = "",
    progress_callback=None,
) -> list[ExtractedPlant]:
    """Extract structured plant monographs from herbal text."""
    cb = progress_callback or (lambda msg: None)
    chunks = _split_into_chunks(text)
    cb(f"Plant extraction: {len(chunks)} chunk(s)")

    all_plants: list[ExtractedPlant] = []
    for i, chunk in enumerate(chunks):
        cb(f"Plant chunk {i+1}/{len(chunks)}: {len(chunk)} chars")
        plants = await _extract_single(chunk, book_title)
        cb(f"Plant chunk {i+1}: got {len(plants)} plants")
        all_plants.extend(plants)
    cb(f"Total extracted: {len(all_plants)} plants")
    return all_plants


async def _extract_single(text: str, book_title: str) -> list[ExtractedPlant]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f'Here is text from the herbal "{book_title}".\n\n'
            f"Extract all plant monographs:\n\n{text}"
        )},
    ]
    result = await chat_completion_json(
        messages, task="plant_extraction", temperature=0.1, max_tokens=32768,
    )
    return [_parse_plant(p) for p in _coerce_list(result) if _is_valid_plant(p)]


def _is_valid_plant(data) -> bool:
    if not isinstance(data, dict):
        return False
    return len(_str(data.get("name"))) >= 2


def _parse_plant(d: dict) -> ExtractedPlant:
    uses = [
        ExtractedMedicinalUse(
            part=_canon_part(u.get("part", "")),
            action=_str(u.get("action")),
            indications=_str(u.get("indications")),
            preparation=_str(u.get("preparation")),
            dosage=_str(u.get("dosage")),
            contraindications=_str(u.get("contraindications")),
            original_text=_str(u.get("original_text")),
        )
        for u in (d.get("medicinal_uses") or []) if isinstance(u, dict)
    ]
    compounds = [
        ExtractedCompound(
            compound=_str(c.get("compound")),
            compound_group=_str(c.get("compound_group")),
            part=_canon_part(c.get("part", "")),
            notes=_str(c.get("notes")),
        )
        for c in (d.get("compounds") or []) if isinstance(c, dict) and _str(c.get("compound"))
    ]
    harvests = [
        ExtractedHarvest(
            part=_canon_part(h.get("part", "")),
            season=_str(h.get("season")),
            method=_str(h.get("method")),
            original_text=_str(h.get("original_text")),
        )
        for h in (d.get("harvests") or []) if isinstance(h, dict)
    ]
    habitats = [
        ExtractedHabitat(
            region=_str(h.get("region")),
            biotope=_str(h.get("biotope")),
            status=_str(h.get("status")),
            original_text=_str(h.get("original_text")),
        )
        for h in (d.get("habitats") or []) if isinstance(h, dict)
    ]
    toxicities = [
        ExtractedToxicity(
            toxic_parts=[_canon_part(p) for p in _str_list(t.get("toxic_parts"))],
            symptoms=_str(t.get("symptoms")),
            antidote=_str(t.get("antidote")),
            severity=_str(t.get("severity")),
            original_text=_str(t.get("original_text")),
        )
        for t in (d.get("toxicities") or []) if isinstance(t, dict)
    ]
    return ExtractedPlant(
        name=_str(d.get("name")),
        name_latin=_str(d.get("name_latin")),
        family=_str(d.get("family")),
        family_latin=_str(d.get("family_latin")),
        names_historical=_str_list(d.get("names_historical")),
        description=_str(d.get("description")),
        parts_used=[_canon_part(p) for p in _str_list(d.get("parts_used"))],
        is_toxic=bool(d.get("is_toxic")) or len(toxicities) > 0,
        original_text=_str(d.get("original_text")),
        medicinal_uses=uses,
        compounds=compounds,
        harvests=harvests,
        habitats=habitats,
        toxicities=toxicities,
    )
