"""Corpus-bootstrap the two medical vocabularies — the *build* pass of the
medical-normalizer (Phase A; ``normalize_medical_uses`` in ``medical_matching``
is Phase B).

Unlike ``compound_extractor`` — which reads ONE property-first phytochemistry book
prose — phytotherapy books are plant-organized and have already filled
``PlantMedicinalUse.action_raw`` and ``.indications`` via the herbalism pipeline.
So there is no book to parse: the raw material is the set of distinct strings the
corpus has already accumulated. This service pulls those distinct strings and asks
the model to **canonicalize** them (not extract), clustering spelling/grammar
variants and — for indications — mapping archaic disease names onto a modern
concept (водянка→отёки, грудная жаба→стенокардия). The result is upserted into
``MedicinalAction`` / ``Indication`` with the same cumulative, never-clobber
discipline as ``_upsert_compound``.

This is reference *canonicalization*, not fact extraction, so the anti-fabrication
grounding guard does NOT apply: the facts (plant X used for водянка) were already
grounded when the herbalism extractor wrote the row; building the
"водянка → отёки" mapping is legitimate reference knowledge. The builder only ever
canonicalizes strings the corpus already contains; it never invents plant↔use facts.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import MedicinalAction, Indication, PlantMedicinalUse
from app.services.llm import chat_completion_json
from app.services.medical_matching import _split_atoms, _norm

_LLM_HEARTBEAT_INTERVAL = 30  # seconds
# Distinct raw terms per LLM call. Each term is short (an action word or a short
# indication phrase), so a larger batch than the prose extractor fits comfortably.
TERMS_PER_BATCH = 150


# --------------------------------------------------------------------------- #
# small coercion helpers (mirror compound_extractor)
# --------------------------------------------------------------------------- #
def _str(v) -> str:
    return (v if isinstance(v, str) else "" if v is None else str(v)).strip()


def _str_list(v) -> list[str]:
    if isinstance(v, list):
        return [_str(x) for x in v if _str(x)]
    s = _str(v)
    return [s] if s else []


def _coerce_list(val, *keys) -> list[dict]:
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        for key in (*keys, "result", "results", "data", "concepts", "items"):
            inner = val.get(key)
            if isinstance(inner, list):
                return inner
        if "name" in val:
            return [val]
        for v in val.values():
            if isinstance(v, list):
                return v
    return []


# --------------------------------------------------------------------------- #
# canonicalization prompts
# --------------------------------------------------------------------------- #
_ACTION_SYSTEM = """You are canonicalizing a controlled vocabulary of medicinal ACTIONS \
(фармакологическое действие) of plants, from a list of raw Russian strings that historical and modern \
phytotherapy books used to describe what a remedy DOES (e.g. "отхаркивающее", "вяжущее", "мочегонным", \
"усиливает отделение мочи").

You are NOT extracting from prose and you are NOT inventing facts — you only group the raw strings I give \
you into canonical action concepts. Cluster spelling/grammatical variants of the same action together.

Return a JSON object {"actions": [ ... ]} whose value is an array of canonical concepts. For each concept:
- name: the canonical Russian action term, nominative neuter adjective form (e.g. "мочегонное", \
"отхаркивающее", "вяжущее"). Prefer the common/historical term where one exists.
- name_modern: the modern/clinical synonym if there is one (мочегонное→"диуретическое", \
отхаркивающее→"экспекторантное"), else "".
- parent: the broader functional group this belongs to, as a Russian phrase (e.g. "действие на \
дыхательную систему", "действие на ЖКТ", "действие на ССС"); "" if this IS a group or has no obvious group.
- system: a SHORT body-system tag — one of: дыхание, ЖКТ, ССС, ЦНС, кожа, мочеполовая, кровь, обмен, \
иммунитет, прочее.
- synonyms: array of the OTHER raw forms/spellings from my list that mean this same action (genitive/\
instrumental forms, verb phrases, OCR variants). Include every raw input string you assign to this concept \
EXCEPT the one chosen as `name`.

Be conservative: if a raw string is too vague to be an action (e.g. "лечебное", "полезное"), drop it. Map \
EVERY clear input to exactly one concept. Do not output concepts for strings I did not give you."""

_INDICATION_SYSTEM = """You are canonicalizing a controlled vocabulary of medicinal INDICATIONS (показания) \
— what a plant remedy is used FOR (symptoms and diseases) — from a list of raw Russian strings taken from \
historical and modern phytotherapy books (e.g. "кашель", "при водянке", "грудная жаба", "золотуха", \
"лихорадка").

You are NOT extracting from prose and you are NOT inventing facts — you only group the raw strings I give \
you into canonical disease/symptom concepts, AND you bridge archaic names to their modern concept. This is \
legitimate reference knowledge (водянка = отёки/асцит; грудная жаба = стенокардия; золотуха = скрофулёз; \
падучая = эпилепсия; антонов огонь = гангрена) — use it.

Return a JSON object {"indications": [ ... ]} whose value is an array of canonical concepts. For each:
- name: the canonical term — MODERN where a modern term exists (e.g. "отёки", "стенокардия", "эпилепсia" \
→ use "эпилепсия"), nominative case. Keep a historical term as `name` only if there is no modern equivalent.
- name_modern: the explicit modern/clinical name when `name` is kept historical, else "".
- parent: the broader group as a Russian phrase (e.g. "болезни органов дыхания", "болезни ЖКТ", \
"кожные болезни", "болезни сердца и сосудов"); "" if this IS a group.
- system: a SHORT body-system tag — one of: дыхание, ЖКТ, ССС, ЦНС, кожа, мочеполовая, кровь, обмен, \
инфекции, прочее.
- synonyms: array of the OTHER raw forms from my list that denote this same concept and are NOT archaic \
(genitive forms like "кашля", prepositional phrases like "при кашле", OCR/spelling variants). Include every \
non-archaic raw input you assign here except the one chosen as `name`.
- archaic: array of the raw forms from my list that are PRE-MODERN names of this concept (водянка, грудная \
жаба, золотуха, ...). This is the bridge — put archaic disease names here, modern variants in `synonyms`.
- definition: a SHORT (one clause) gloss of the concept, only if useful; else "".

Be conservative: drop strings too vague to be an indication ("болезни", "разные недуги"). Map EVERY clear \
input to exactly one concept. Do not output concepts for strings I did not give you."""


# --------------------------------------------------------------------------- #
# upserts — cumulative, never-clobber (mirror activities._upsert_compound)
# --------------------------------------------------------------------------- #
def _merge_arr(existing: list[str] | None, incoming: list[str]) -> list[str]:
    merged = list(existing or [])
    seen = {x.lower() for x in merged}
    for s in incoming:
        if s and s.lower() not in seen:
            merged.append(s)
            seen.add(s.lower())
    return merged


async def _upsert_action(db, *, name, name_modern="", system="", synonyms=None,
                         parent_id=None, source_book_id=None) -> MedicinalAction | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = (await db.execute(
        select(MedicinalAction).where(MedicinalAction.name.ilike(name))
    )).scalars().first()
    if existing is None:
        a = MedicinalAction(
            name=name, name_modern=(name_modern or None), system=(system[:50] if system else None),
            synonyms=(synonyms or None), parent_id=parent_id, source_book_id=source_book_id,
        )
        db.add(a)
        await db.flush()
        return a
    if not existing.name_modern and name_modern:
        existing.name_modern = name_modern
    if not existing.system and system:
        existing.system = system[:50]
    if existing.parent_id is None and parent_id is not None and parent_id != existing.id:
        existing.parent_id = parent_id
    if synonyms:
        existing.synonyms = _merge_arr(existing.synonyms, synonyms)
    return existing


async def _upsert_indication(db, *, name, name_modern="", system="", synonyms=None,
                             archaic=None, definition="", parent_id=None,
                             source_book_id=None) -> Indication | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = (await db.execute(
        select(Indication).where(Indication.name.ilike(name))
    )).scalars().first()
    if existing is None:
        i = Indication(
            name=name, name_modern=(name_modern or None), system=(system[:50] if system else None),
            synonyms=(synonyms or None), archaic=(archaic or None),
            definition=(definition or None), parent_id=parent_id, source_book_id=source_book_id,
        )
        db.add(i)
        await db.flush()
        return i
    if not existing.name_modern and name_modern:
        existing.name_modern = name_modern
    if not existing.system and system:
        existing.system = system[:50]
    if not existing.definition and definition:
        existing.definition = definition
    if existing.parent_id is None and parent_id is not None and parent_id != existing.id:
        existing.parent_id = parent_id
    if synonyms:
        existing.synonyms = _merge_arr(existing.synonyms, synonyms)
    if archaic:
        existing.archaic = _merge_arr(existing.archaic, archaic)
    return existing


# --------------------------------------------------------------------------- #
# LLM plumbing
# --------------------------------------------------------------------------- #
async def _with_heartbeat(coro, cb, label):
    task = asyncio.ensure_future(coro)
    waited = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=_LLM_HEARTBEAT_INTERVAL)
        if done:
            return await task
        waited += _LLM_HEARTBEAT_INTERVAL
        cb(f"{label} (LLM working {waited}s)")


def _batches(terms: list[str], size: int) -> list[list[str]]:
    return [terms[i:i + size] for i in range(0, len(terms), size)]


async def _canonicalize(system_prompt: str, axis_key: str, terms: list[str], task: str) -> list[dict]:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(terms))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            "Canonicalize these raw terms into concepts. Cluster variants of the same "
            f"concept together; map every clear term.\n\n{numbered}"
        )},
    ]
    result = await chat_completion_json(messages, task=task, temperature=0.1, max_tokens=16384)
    return _coerce_list(result, axis_key)


# --------------------------------------------------------------------------- #
# Phase A entry point
# --------------------------------------------------------------------------- #
async def build_medical_vocab(db: AsyncSession, commit: bool = True, progress_callback=None) -> dict:
    """Corpus-bootstrap the action + indication vocabularies from the distinct
    raw strings already in ``plant_medicinal_uses``. Idempotent and cumulative:
    re-running after a new phytotherapy book folds its new raw terms in without
    clobbering existing concepts."""
    cb = progress_callback or (lambda msg: None)

    # ---- gather distinct raw material from the corpus ----
    action_rows = (await db.execute(
        select(PlantMedicinalUse.action_raw).where(PlantMedicinalUse.action_raw.isnot(None)).distinct()
    )).scalars().all()
    actions_raw = sorted({a.strip() for a in action_rows if a and len(_norm(a)) >= 2})

    ind_rows = (await db.execute(
        select(PlantMedicinalUse.indications).where(PlantMedicinalUse.indications.isnot(None)).distinct()
    )).scalars().all()
    ind_atoms: dict[str, str] = {}  # normalized -> first-seen surface form
    for field in ind_rows:
        for atom in _split_atoms(field):
            ind_atoms.setdefault(_norm(atom), atom)
    indications_raw = sorted(ind_atoms.values())

    cb(f"Distinct action_raw: {len(actions_raw)}; distinct indication atoms: {len(indications_raw)}")

    # ---- Phase A.1: actions ----
    actions_created = 0
    a_batches = _batches(actions_raw, TERMS_PER_BATCH)
    for bi, batch in enumerate(a_batches):
        cb(f"Action batch {bi+1}/{len(a_batches)} ({len(batch)} terms)")
        concepts = await _with_heartbeat(
            _canonicalize(_ACTION_SYSTEM, "actions", batch, "medical_action_vocab"),
            cb, f"Action batch {bi+1}/{len(a_batches)}")
        for c in concepts:
            if not isinstance(c, dict) or len(_str(c.get("name"))) < 2:
                continue
            parent_id = None
            parent = _str(c.get("parent"))
            if parent and parent.lower() != _str(c.get("name")).lower():
                p = await _upsert_action(db, name=parent, system=_str(c.get("system")))
                parent_id = p.id if p else None
            a = await _upsert_action(
                db, name=_str(c.get("name")), name_modern=_str(c.get("name_modern")),
                system=_str(c.get("system")), synonyms=_str_list(c.get("synonyms")),
                parent_id=parent_id)
            if a is not None:
                actions_created += 1
        if commit:
            await db.commit()

    # ---- Phase A.2: indications ----
    indications_created = 0
    i_batches = _batches(indications_raw, TERMS_PER_BATCH)
    for bi, batch in enumerate(i_batches):
        cb(f"Indication batch {bi+1}/{len(i_batches)} ({len(batch)} terms)")
        concepts = await _with_heartbeat(
            _canonicalize(_INDICATION_SYSTEM, "indications", batch, "medical_indication_vocab"),
            cb, f"Indication batch {bi+1}/{len(i_batches)}")
        for c in concepts:
            if not isinstance(c, dict) or len(_str(c.get("name"))) < 2:
                continue
            parent_id = None
            parent = _str(c.get("parent"))
            if parent and parent.lower() != _str(c.get("name")).lower():
                p = await _upsert_indication(db, name=parent, system=_str(c.get("system")))
                parent_id = p.id if p else None
            i = await _upsert_indication(
                db, name=_str(c.get("name")), name_modern=_str(c.get("name_modern")),
                system=_str(c.get("system")), synonyms=_str_list(c.get("synonyms")),
                archaic=_str_list(c.get("archaic")), definition=_str(c.get("definition")),
                parent_id=parent_id)
            if i is not None:
                indications_created += 1
        if commit:
            await db.commit()

    action_total = (await db.execute(select(MedicinalAction))).scalars().all()
    indication_total = (await db.execute(select(Indication))).scalars().all()
    return {
        "action_terms_in": len(actions_raw),
        "indication_terms_in": len(indications_raw),
        "actions_upserted": actions_created,
        "indications_upserted": indications_created,
        "action_vocab_total": len(action_total),
        "indication_vocab_total": len(indication_total),
    }
