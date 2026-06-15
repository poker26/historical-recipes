"""LLM-based extraction of essential-oil monographs from an aromatherapy book
(the ``aromatherapy`` domain).

The source is organized BY OIL: each section is one named essential oil
(лавандовое масло, масло чайного дерева, масло мяты перечной) describing its
source plant, how it is obtained, its scent, and — the payload — its therapeutic
actions, the conditions it is applied for, how it is applied (ингаляция / массаж
/ ванна / аромалампа / компресс), dosage and contraindications.

Output is oil *monographs*: a canonical oil name, its source plant (bridged to
the herbarium downstream), descriptive fields, and a list of grounded use-facts.
The same anti-fabrication discipline as the plant/compound extractors applies —
aromatherapy evidence is weak and the model is fluent in it, so every fact must
be verbatim-traceable to the text.
"""

import asyncio
import re
from dataclasses import dataclass, field

from app.services.llm import chat_completion_json

_LLM_HEARTBEAT_INTERVAL = 30  # seconds; see plant_extractor for rationale

# Aromatherapy prose is lighter than phytochemistry but oil sections can be long;
# keep input modest so the structured JSON output stays inside the token budget.
MAX_CHARS_PER_CALL = 14_000


@dataclass
class ExtractedOilUse:
    """One therapeutic statement about the oil."""
    action: str = ""             # pharmacological action as written (bridged to medicinal_actions later)
    indications: str = ""        # what it is used for (free text, bridged to indications later)
    application: str = ""        # ингаляция/массаж/ванна/аромалампа/компресс/внутрь/наружно
    dosage: str = ""
    contraindications: str = ""
    original_text: str = ""      # verbatim anchor — REQUIRED


@dataclass
class ExtractedOil:
    name: str
    name_latin: str = ""
    source_plant: str = ""       # Russian plant name the oil is distilled from
    source_plant_latin: str = ""
    part: str = ""               # part the oil comes from (цвет/лист/плод/кора/древесина/смола...)
    extraction: str = ""         # дистилляция/отжим/экстракция/анфлераж
    aroma_profile: str = ""      # описание запаха / ноты
    synonyms: list[str] = field(default_factory=list)
    description: str = ""
    original_text: str = ""      # verbatim anchor for the oil entry
    uses: list[ExtractedOilUse] = field(default_factory=list)


SYSTEM_PROMPT = """You are extracting structured monographs of ESSENTIAL OILS (эфирные масла) from a Russian \
aromatherapy / essential-oils reference. The book is organized BY OIL — each section describes ONE essential oil: \
the plant/material it is obtained from, how it is produced, its scent, and how it is used therapeutically.

Sections are headed in one of TWO conventions — treat BOTH as an oil entry:
  (a) by the OIL name, e.g. «Лавандовое масло», «Масло чайного дерева», «Масло мяты перечной»; OR
  (b) by the SOURCE plant / material name, e.g. «Лаванда», «Альпиния», «Кедр атласский», «Чайное дерево», \
«Перец» — in an essential-oils reference such a heading denotes THAT plant's essential oil, and the section \
body is about the oil (получение/аромат/состав/применение). Extract these exactly the same way — a \
plant/material heading here IS an oil entry, NOT something to skip.

Extract EVERY essential oil the text describes — ONE object per section/entry. For each return an object:
- name: Russian name of the OIL in nominative case. If the text gives an explicit oil name, use it \
(e.g. "лавандовое масло", "масло чайного дерева"). If the section is headed only by the source plant/material \
(convention (b) above), derive the oil name from that heading — e.g. heading «Альпиния» → "масло альпинии", \
«Кедр атласский» → "масло кедра атласского", «Перец» → "масло перца" (or the «X-овое масло» form when natural). \
Always record the plant itself in source_plant. Do NOT skip an entry just because its heading is a bare plant name.
- name_latin: pharmacopoeial Latin name if given (e.g. "Oleum Lavandulae"), else ""
- source_plant: the Russian name of the plant the oil is distilled/pressed from, if stated, else ""
- source_plant_latin: Latin binomial of the source plant if given, else ""
- part: the plant part the oil comes from (цвет/соцветие/лист/трава/плод/кожура/семя/кора/древесина/корень/смола), \
if stated, else ""
- extraction: how the oil is obtained (паровая дистилляция/перегонка/отжим/холодное прессование/экстракция/\
анфлераж), if stated, else ""
- aroma_profile: a SHORT description of the smell / scent notes, only if the text states it, else ""
- synonyms: array of alternate names/spellings of the oil, else []
- description: a SHORT general description of the oil, only if stated, else ""
- original_text: a VERBATIM quote from the text introducing this oil — the anchor
- uses: array of therapeutic-use facts the text states for this oil. Each:
    - action: the therapeutic action as written (e.g. "успокаивающее", "антисептическое", "спазмолитическое"), \
else ""
    - indications: what it is used FOR — conditions/symptoms as written (e.g. "бессонница, тревога, головная боль"), \
else ""
    - application: how it is applied (ингаляция/массаж/ванна/аромалампа/компресс/растирание/внутрь/наружно), else ""
    - dosage: dose / number of drops / proportion, as written, else ""
    - contraindications: cautions as written (фототоксичность, беременность, аллергия, для детей...), else ""
    - original_text: the exact source sentence stating this use

CRITICAL — extract only what is literally present; never use your own knowledge:
- You KNOW aromatherapy (that lavender calms, tea-tree is antiseptic, etc.). IGNORE that. Extract an oil, an \
action, an indication, an application or a contraindication ONLY if it is explicitly stated in the text above.
- Every object and every use MUST carry an original_text that is a LITERAL quote from the text above. If you \
cannot quote the source for it, do NOT emit it.
- Preserve original_text VERBATIM (same wording AND orthography); for a plant/material-headed entry, quote the \
heading or the sentence introducing its oil as the anchor. Do NOT extract table-of-contents lines or general \
front-matter essays about aromatherapy in the abstract (history, methods, safety-in-general) that describe no \
specific oil — but a section headed by a plant/material name DOES describe a specific oil and MUST be extracted.

Return a JSON object with a single key "oils" whose value is an ARRAY of oil objects: \
{"oils": [ {"name": ...}, {"name": ...} ]}."""


def _str(v) -> str:
    return (v if isinstance(v, str) else "" if v is None else str(v)).strip()


def _str_list(v) -> list[str]:
    if isinstance(v, list):
        return [_str(x) for x in v if _str(x)]
    s = _str(v)
    return [s] if s else []


def _coerce_list(val) -> list[dict]:
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for key in ("oils", "essential_oils", "result", "results", "data"):
            inner = val.get(key)
            if isinstance(inner, list):
                return inner
        if "name" in val:
            return [val]
        for v in val.values():
            if isinstance(v, list):
                return v
    return []


def _split_into_chunks(text: str) -> list[str]:
    """Size-based split at line breaks (same as the compound extractor): an oil
    reference has section headers we don't reliably detect, so we chunk by size
    and let each call extract whatever oils fall inside its window."""
    if len(text) <= MAX_CHARS_PER_CALL:
        return [text]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in text.split("\n"):
        buf.append(line)
        buf_len += len(line) + 1
        if buf_len >= MAX_CHARS_PER_CALL:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _norm_for_match(t: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", (t or "").lower(), flags=re.UNICODE).strip() + " "


def _is_grounded(text: str, source_norm: str, shingle: int = 6) -> bool:
    """True if ``text`` is verbatim-traceable to the (pre-normalized) source.
    Mirrors the plant/compound extractor guard."""
    words = _norm_for_match(text).split()
    if not words:
        return False
    k = min(shingle, len(words))
    return any(
        " " + " ".join(words[i:i + k]) + " " in source_norm
        for i in range(0, len(words) - k + 1)
    )


def _name_in_source(name: str, source_norm: str) -> bool:
    words = _norm_for_match(name).split()
    if not words:
        return False
    head = words[0]
    if len(head) < 4:
        return f" {head} " in source_norm
    stem = head[: max(4, len(head) - 2)]
    return f" {stem}" in source_norm


async def extract_oils_from_text(
    text: str,
    book_title: str = "",
    progress_callback=None,
) -> list[ExtractedOil]:
    cb = progress_callback or (lambda msg: None)
    chunks = _split_into_chunks(text)
    cb(f"Oil extraction: {len(chunks)} chunk(s)")
    out: list[ExtractedOil] = []
    for i, chunk in enumerate(chunks):
        cb(f"Oil chunk {i+1}/{len(chunks)}: {len(chunk)} chars")
        oils = await _with_heartbeat(
            _extract_single(chunk, book_title), cb, f"Oil chunk {i+1}/{len(chunks)}"
        )
        cb(f"Oil chunk {i+1}: got {len(oils)} oils")
        out.extend(oils)
    cb(f"Total extracted: {len(out)} oils")
    return out


async def _with_heartbeat(coro, cb, label):
    task = asyncio.ensure_future(coro)
    waited = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=_LLM_HEARTBEAT_INTERVAL)
        if done:
            return await task
        waited += _LLM_HEARTBEAT_INTERVAL
        cb(f"{label} (LLM working {waited}s)")


async def _extract_single(text: str, book_title: str) -> list[ExtractedOil]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f'Here is text from the aromatherapy reference "{book_title}".\n\n'
            f"Extract all essential-oil entries:\n\n{text}"
        )},
    ]
    result = await chat_completion_json(
        messages, task="aroma_extraction", temperature=0.1, max_tokens=32768,
    )
    source_norm = _norm_for_match(text)
    grounded: list[ExtractedOil] = []
    for o in _coerce_list(result):
        if not isinstance(o, dict) or len(_str(o.get("name"))) < 3:
            continue
        oil = _ground_oil(_parse_oil(o), source_norm)
        if oil is not None:
            grounded.append(oil)
    return grounded


def _parse_oil(d: dict) -> ExtractedOil:
    uses = [
        ExtractedOilUse(
            action=_str(u.get("action")),
            indications=_str(u.get("indications")),
            application=_str(u.get("application")),
            dosage=_str(u.get("dosage")),
            contraindications=_str(u.get("contraindications")),
            original_text=_str(u.get("original_text")),
        )
        for u in (d.get("uses") or []) if isinstance(u, dict)
    ]
    return ExtractedOil(
        name=_str(d.get("name")),
        name_latin=_str(d.get("name_latin")),
        source_plant=_str(d.get("source_plant")),
        source_plant_latin=_str(d.get("source_plant_latin")),
        part=_str(d.get("part")),
        extraction=_str(d.get("extraction")),
        aroma_profile=_str(d.get("aroma_profile")),
        synonyms=_str_list(d.get("synonyms")),
        description=_str(d.get("description")),
        original_text=_str(d.get("original_text")),
        uses=uses,
    )


def _ground_oil(o: ExtractedOil, source_norm: str) -> ExtractedOil | None:
    """Drop fabricated uses; drop the whole oil if untraceable.

    A use survives only if its ``original_text`` is grounded. The oil itself is
    kept if its name appears in the source, its entry quote / description is
    grounded, or at least one use survived — otherwise it is treated as
    recited-from-memory and discarded.
    """
    o.uses = [u for u in o.uses if _is_grounded(u.original_text, source_norm)]
    anchored = (
        _name_in_source(o.name, source_norm)
        or _is_grounded(o.original_text, source_norm)
        or _is_grounded(o.description, source_norm)
        or bool(o.uses)
    )
    return o if anchored else None
