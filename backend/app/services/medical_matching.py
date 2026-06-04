"""Normalize free-text medical fields on ``PlantMedicinalUse`` to the controlled
medical vocabularies — the corpus-wide normalize pass of the medical-normalizer.

Two axes, both idempotent full recomputes (the medical analog of
``compound_matching.normalize_plant_compounds``):

- ``action_raw`` → ``action_id`` (single ``MedicinalAction``);
- the free-text ``indications`` field (often several, "лихорадка, кашель") is
  split into atoms and each is mapped to an ``Indication`` concept, the resolved
  set stored in ``indication_ids``.

Matching discipline mirrors the compound matcher's conservatism: normalized exact
on name / modern name / synonyms (and, for indications, the ``archaic`` bridge
names) first, then a token-subset fallback preferring the MOST specific vocabulary
term fully contained in the string. An ambiguous tie is left unmapped — a wrong
normalize is worse than an unnormalized stub.
"""

import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import MedicinalAction, Indication, PlantMedicinalUse

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
# Split a free-text indications field into atoms: commas, semicolons, slashes,
# newlines, and standalone conjunctions ("кашель и насморк").
_ATOM_SPLIT_RE = re.compile(r"\s*(?:[,;/\n]|\bи\b|\bили\b)\s*", re.IGNORECASE)


def _norm(s: str | None) -> str:
    return " ".join(_WORD_RE.findall((s or "").lower()))


def _tokens(s: str | None) -> frozenset[str]:
    return frozenset(_WORD_RE.findall((s or "").lower()))


def _split_atoms(s: str | None) -> list[str]:
    """Break a free-text indications field into individual indication phrases."""
    out: list[str] = []
    for part in _ATOM_SPLIT_RE.split(s or ""):
        p = part.strip(" .—-—()[]")
        if len(_norm(p)) >= 2:
            out.append(p)
    return out


def _build_index(vocab, extra_key_attrs=()):
    """exact: normalized name/synonym/... -> set of ids; tokens: (id, token set).

    ``extra_key_attrs`` names additional list/str attributes whose values are also
    exact keys (e.g. ``name_modern``, ``synonyms``, ``archaic``).
    """
    exact: dict[str, set[uuid.UUID]] = {}
    token_index: list[tuple[uuid.UUID, frozenset[str]]] = []
    for v in vocab:
        keys: list[str] = [v.name]
        for attr in extra_key_attrs:
            val = getattr(v, attr, None)
            if isinstance(val, (list, tuple)):
                keys.extend(val)
            elif val:
                keys.append(val)
        for key in keys:
            nk = _norm(key)
            if nk:
                exact.setdefault(nk, set()).add(v.id)
        toks = _tokens(v.name)
        if toks:
            token_index.append((v.id, toks))
    return exact, token_index


def _match_one(raw: str | None, exact, token_index) -> uuid.UUID | None:
    nk = _norm(raw)
    if nk:
        ids = exact.get(nk)
        if ids and len(ids) == 1:
            return next(iter(ids))
    rtoks = _tokens(raw)
    if not rtoks:
        return None
    contained = [(vid, vt) for vid, vt in token_index if vt <= rtoks]
    if contained:
        maxlen = max(len(vt) for _, vt in contained)
        best = [vid for vid, vt in contained if len(vt) == maxlen]
        if len(best) == 1:
            return best[0]
    return None


async def normalize_medical_uses(db: AsyncSession, commit: bool = True) -> dict:
    """Map every ``PlantMedicinalUse`` to a normalized action + indication set.
    Idempotent corpus-wide recompute."""
    actions = (await db.execute(select(MedicinalAction))).scalars().all()
    indications = (await db.execute(select(Indication))).scalars().all()
    a_exact, a_tokens = _build_index(actions, extra_key_attrs=("name_modern", "synonyms"))
    i_exact, i_tokens = _build_index(indications, extra_key_attrs=("name_modern", "synonyms", "archaic"))

    # Authoritative recompute: clear existing links so stale ones from an earlier
    # vocabulary are dropped.
    await db.execute(update(PlantMedicinalUse).values(action_id=None, indication_ids=[]))

    uses = (await db.execute(select(PlantMedicinalUse))).scalars().all()
    actions_linked = 0
    indications_linked = 0
    uses_with_indication = 0
    for u in uses:
        aid = _match_one(u.action_raw, a_exact, a_tokens)
        if aid is not None:
            u.action_id = aid
            actions_linked += 1
        ids: list[uuid.UUID] = []
        for atom in _split_atoms(u.indications):
            iid = _match_one(atom, i_exact, i_tokens)
            if iid is not None and iid not in ids:
                ids.append(iid)
        if ids:
            u.indication_ids = ids
            uses_with_indication += 1
            indications_linked += len(ids)

    if commit:
        await db.commit()
    return {
        "uses_total": len(uses),
        "actions_linked": actions_linked,
        "uses_with_indication": uses_with_indication,
        "indication_links": indications_linked,
        "action_vocab": len(actions),
        "indication_vocab": len(indications),
    }
