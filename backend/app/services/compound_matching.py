"""Normalize free-text ``PlantCompound.compound`` strings to the controlled
``compounds`` vocabulary — the corpus-wide ``normalize_corpus`` step of the
reference-normalizer pipeline.

Idempotent full recompute (the compound analog of ``relink_recipe_ingredients``):
clears every ``compound_id`` and re-derives it against the current vocabulary, so
it is safe to re-run whenever the vocabulary grows from a new reference book.

Matching discipline mirrors the plant matcher's conservatism — normalized exact
on name/synonyms first, then a token-subset fallback that prefers the MOST
specific vocabulary term fully contained in the string. An ambiguous match
(two equally specific candidates) is left NULL: a wrong normalize is worse than
an unnormalized stub.
"""

import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import Compound, PlantCompound

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _norm(s: str | None) -> str:
    return " ".join(_WORD_RE.findall((s or "").lower()))


# Compound classes that are NOT plant constituents we surface as «химический состав»:
# functional enzymes/coenzymes, plant hormones, and synthetic substances the extractor
# grabbed from metabolism/process discussions in reference books. Vitamins, minerals,
# amino acids, proteins ARE kept (real constituents) — user decision 2026-06-16. The
# class pattern auto-suppresses future compounds of these classes too; reversible.
_NONTARGET_CLASS_RE = re.compile(r"фермент|синтетич|гормон", re.IGNORECASE)


def is_nontarget_compound_class(compound_class: str | None) -> bool:
    """True for enzyme / synthetic / hormone classes — suppressed from a plant's
    displayed chemical composition (NOT deleted; just hidden, reversible)."""
    return bool(compound_class and _NONTARGET_CLASS_RE.search(compound_class))


def _tokens(s: str | None) -> frozenset[str]:
    return frozenset(_WORD_RE.findall((s or "").lower()))


def _build_index(vocab: list[Compound]):
    """exact: normalized name/synonym -> set of compound ids; tokens: (id, token set)."""
    exact: dict[str, set[uuid.UUID]] = {}
    token_index: list[tuple[uuid.UUID, frozenset[str]]] = []
    for c in vocab:
        for key in (c.name, *(c.synonyms or [])):
            nk = _norm(key)
            if nk:
                exact.setdefault(nk, set()).add(c.id)
        toks = _tokens(c.name)
        if toks:
            token_index.append((c.id, toks))
    return exact, token_index


def _match_one(compound: str | None, group: str | None, exact, token_index) -> uuid.UUID | None:
    # 1) normalized exact match on the raw compound, then its group tag.
    for cand in (compound, group):
        nk = _norm(cand)
        if nk:
            ids = exact.get(nk)
            if ids and len(ids) == 1:
                return next(iter(ids))
    # 2) token-subset: a vocabulary term whose tokens are all present in the
    #    compound string. Prefer the most specific (largest token set); bail on a
    #    tie between equally specific candidates.
    ctoks = _tokens(compound)
    if not ctoks:
        return None
    contained = [(cid, vt) for cid, vt in token_index if vt <= ctoks]
    if contained:
        maxlen = max(len(vt) for _, vt in contained)
        best = [cid for cid, vt in contained if len(vt) == maxlen]
        if len(best) == 1:
            return best[0]
    return None


async def normalize_plant_compounds(db: AsyncSession, commit: bool = True) -> dict:
    """Map every ``PlantCompound.compound`` to a ``compound_id``. Idempotent."""
    vocab = (await db.execute(select(Compound))).scalars().all()
    exact, token_index = _build_index(vocab)

    # Authoritative recompute: clear existing links so stale/incorrect ones from
    # an earlier vocabulary are dropped.
    await db.execute(update(PlantCompound).values(compound_id=None))

    pcs = (await db.execute(select(PlantCompound))).scalars().all()
    linked = 0
    for pc in pcs:
        cid = _match_one(pc.compound, pc.compound_group, exact, token_index)
        if cid is not None:
            pc.compound_id = cid
            linked += 1

    if commit:
        await db.commit()
    return {"linked_compounds": linked, "total": len(pcs), "vocab_size": len(vocab)}
