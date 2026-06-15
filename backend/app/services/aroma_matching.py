"""Normalize the free-text medical fields on ``EssentialOilUse`` to the SAME
controlled vocabularies the plant layer uses — the corpus-wide normalize pass of
the aromatherapy pipeline.

This is the oil analog of ``medical_matching.normalize_medical_uses`` and reuses
its exact machinery (``_build_index`` / ``_match_one`` / ``_split_atoms``):

- ``action_raw`` → ``action_id`` (single ``MedicinalAction``);
- the free-text ``indications`` field → ``indication_ids`` (one or more
  ``Indication`` concepts).

Because oils normalize against the very same ``medicinal_actions`` /
``indications`` vocabularies as plants, a "what helps with X" query reaches both
herbs and oils on one axis. Idempotent full recompute, conservative matching (an
ambiguous tie is left unmapped — a wrong normalize is worse than a stub). Oil-use
strings whose action/indication is not yet in the vocabulary simply stay NULL
until the medical vocabulary is grown.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import MedicinalAction, Indication, EssentialOilUse
from app.services.medical_matching import _build_index, _match_one, _split_atoms


async def normalize_oil_uses(db: AsyncSession, commit: bool = True) -> dict:
    """Map every ``EssentialOilUse`` to a normalized action + indication set
    against the existing medical vocabularies. Idempotent corpus-wide recompute."""
    actions = (await db.execute(select(MedicinalAction))).scalars().all()
    indications = (await db.execute(select(Indication))).scalars().all()
    a_exact, a_tokens = _build_index(actions, extra_key_attrs=("name_modern", "synonyms"))
    i_exact, i_tokens = _build_index(indications, extra_key_attrs=("name_modern", "synonyms", "archaic"))

    # Authoritative recompute: clear existing links so stale ones from an earlier
    # vocabulary are dropped.
    await db.execute(update(EssentialOilUse).values(action_id=None, indication_ids=[]))

    uses = (await db.execute(select(EssentialOilUse))).scalars().all()
    actions_linked = 0
    indications_linked = 0
    uses_with_indication = 0
    for u in uses:
        aid = _match_one(u.action_raw, a_exact, a_tokens)
        if aid is not None:
            u.action_id = aid
            actions_linked += 1
        ids = []
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
        "oil_uses_total": len(uses),
        "actions_linked": actions_linked,
        "uses_with_indication": uses_with_indication,
        "indication_links": indications_linked,
        "action_vocab": len(actions),
        "indication_vocab": len(indications),
    }
