"""LLM-based extraction of a chemical-compound vocabulary from a phytochemistry
reference (the ``reference`` domain's first instance).

Unlike the plant extractor, the source here is organized BY compound class, not
by plant — e.g. «Биологически активные вещества лекарственных растений»
(Георгиевский и др.), with chapters on Алкалоиды / Сердечные гликозиды /
Флавоноиды / Сапонины / Кумарины / Дубильные вещества / Эфирные масла / Витамины.

Output is compound *monographs*: a canonical term, its parent class, synonyms, a
short definition, the pharmacological action it has (free text, bridged to
medicinal actions downstream), and the plants the source states contain it. The
same anti-fabrication grounding discipline as the plant/recipe extractors applies
— the model knows this domain cold, so every fact must be verbatim-traceable.
"""

import asyncio
import re
from dataclasses import dataclass, field

from app.services.llm import chat_completion_json

_LLM_HEARTBEAT_INTERVAL = 30  # seconds; see plant_extractor for rationale

# Dense scientific prose; keep input modest so the structured JSON output stays
# inside the response-token budget for token-dense Cyrillic.
MAX_CHARS_PER_CALL = 14_000


@dataclass
class ExtractedCompoundOccurrence:
    """A stated occurrence of the compound in a plant (+ part)."""
    plant: str = ""
    plant_latin: str = ""
    part: str = ""
    original_text: str = ""


@dataclass
class ExtractedCompound:
    name: str
    name_latin: str = ""
    parent: str = ""               # broader class this belongs to, e.g. рутин -> флавоноиды
    compound_class: str = ""       # top-level class tag (алкалоиды/гликозиды/...)
    synonyms: list[str] = field(default_factory=list)
    definition: str = ""
    action: str = ""               # pharmacological action as written (bridged later)
    original_text: str = ""
    occurrences: list[ExtractedCompoundOccurrence] = field(default_factory=list)


SYSTEM_PROMPT = """You are extracting a structured vocabulary of plant chemical constituents \
(биологически активные вещества) from a Russian phytochemistry reference. The book is organized BY CLASS OF \
SUBSTANCE — chapters such as Алкалоиды, Сердечные гликозиды, Флавоноиды, Сапонины, Кумарины, Дубильные \
вещества, Эфирные масла, Витамины — describing the chemistry of each class, its classification, and which \
plants contain it.

Extract EVERY distinct compound or compound-class the text defines or describes. For each return an object:
- name: canonical Russian term in nominative case (e.g. "сердечные гликозиды", "рутин", "дубильные вещества")
- name_latin: Latin / IUPAC / transliterated name if given, else ""
- parent: the broader class this belongs to, as named in the text (e.g. рутин -> "флавоноиды"; \
сердечные гликозиды -> "гликозиды"); "" for a top-level class
- compound_class: the top-level class tag (алкалоиды / гликозиды / флавоноиды / сапонины / кумарины / \
дубильные вещества / эфирные масла / витамины / органические кислоты / ...), else ""
- synonyms: array of alternate names/spellings the text gives, else []
- definition: a SHORT description of what the substance/class is, in plain text — only if the text states it
- action: the pharmacological action attributed to it, as written (e.g. "кардиотоническое", "вяжущее"), else ""
- original_text: a VERBATIM quote from the text above defining/introducing this compound — the anchor
- occurrences: array of plants the text states contain this compound. Each:
    - plant: Russian plant name in nominative case
    - plant_latin: Latin binomial if given, else ""
    - part: plant part if stated (лист/корень/плод/семя/кора/трава/цвет...), else ""
    - original_text: the exact source sentence stating this plant contains the compound

CRITICAL — extract only what is literally present; never use your own knowledge:
- You KNOW phytochemistry (which alkaloids are in belladonna, etc.). IGNORE that. Extract a compound, a \
definition, an action or a plant occurrence ONLY if it is explicitly stated in the text above.
- Every object and every occurrence MUST carry an original_text that is a LITERAL quote from the text above. \
If you cannot quote the source for it, do NOT emit it.
- Preserve original_text VERBATIM (same wording AND orthography). Do NOT extract table-of-contents lines, \
chapter intros with no substance, or general essays naming no compound.

Return a JSON object with a single key "compounds" whose value is an ARRAY of compound objects: \
{"compounds": [ {"name": ...}, {"name": ...} ]}."""


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
        for key in ("compounds", "result", "results", "data"):
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
    """Size-based split. A phytochemistry book has no per-entity headers to split
    on (it is organized by chapter/class), so we chunk by size at line breaks and
    let each call extract whatever compounds fall inside its window."""
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
    Mirrors the plant/recipe extractor guard: a genuine fact copies its quote out
    of the chunk, so a short word-shingle must reappear. Short phrases (a one-word
    compound name) match on a 1-gram, which still requires the term to occur."""
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


async def extract_compounds_from_text(
    text: str,
    book_title: str = "",
    progress_callback=None,
) -> list[ExtractedCompound]:
    cb = progress_callback or (lambda msg: None)
    chunks = _split_into_chunks(text)
    cb(f"Compound extraction: {len(chunks)} chunk(s)")
    out: list[ExtractedCompound] = []
    for i, chunk in enumerate(chunks):
        cb(f"Compound chunk {i+1}/{len(chunks)}: {len(chunk)} chars")
        comps = await _with_heartbeat(
            _extract_single(chunk, book_title), cb, f"Compound chunk {i+1}/{len(chunks)}"
        )
        cb(f"Compound chunk {i+1}: got {len(comps)} compounds")
        out.extend(comps)
    cb(f"Total extracted: {len(out)} compounds")
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


async def _extract_single(text: str, book_title: str) -> list[ExtractedCompound]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f'Here is text from the phytochemistry reference "{book_title}".\n\n'
            f"Extract all compound entries:\n\n{text}"
        )},
    ]
    result = await chat_completion_json(
        messages, task="compound_extraction", temperature=0.1, max_tokens=32768,
    )
    source_norm = _norm_for_match(text)
    grounded: list[ExtractedCompound] = []
    for c in _coerce_list(result):
        if not isinstance(c, dict) or len(_str(c.get("name"))) < 2:
            continue
        comp = _ground_compound(_parse_compound(c), source_norm)
        if comp is not None:
            grounded.append(comp)
    return grounded


def _parse_compound(d: dict) -> ExtractedCompound:
    occ = [
        ExtractedCompoundOccurrence(
            plant=_str(o.get("plant")),
            plant_latin=_str(o.get("plant_latin")),
            part=_str(o.get("part")),
            original_text=_str(o.get("original_text")),
        )
        for o in (d.get("occurrences") or []) if isinstance(o, dict) and _str(o.get("plant"))
    ]
    return ExtractedCompound(
        name=_str(d.get("name")),
        name_latin=_str(d.get("name_latin")),
        parent=_str(d.get("parent")),
        compound_class=_str(d.get("compound_class")),
        synonyms=_str_list(d.get("synonyms")),
        definition=_str(d.get("definition")),
        action=_str(d.get("action")),
        original_text=_str(d.get("original_text")),
        occurrences=occ,
    )


def _ground_compound(c: ExtractedCompound, source_norm: str) -> ExtractedCompound | None:
    """Drop fabricated plant occurrences; drop the whole compound if untraceable.

    A plant occurrence survives only if its ``original_text`` is grounded. The
    compound itself is kept if its name appears in the source, its definition /
    entry quote is grounded, or at least one occurrence survived — otherwise it is
    treated as recited-from-memory and discarded.
    """
    c.occurrences = [o for o in c.occurrences if _is_grounded(o.original_text, source_norm)]
    anchored = (
        _name_in_source(c.name, source_norm)
        or _is_grounded(c.original_text, source_norm)
        or _is_grounded(c.definition, source_norm)
        or bool(c.occurrences)
    )
    return c if anchored else None
