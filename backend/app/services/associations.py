"""Co-occurrence analysis across the chemistry ↔ medicine axes.

The corpus links a plant to the COMPOUNDS it contains (``PlantCompound``) and,
separately, to the INDICATIONS / ACTIONS it is used for (``PlantMedicinalUse``). It
never states "compound X treats condition Y" directly — so we can only *infer* such a
link through the plant that bridges them. This module computes that bridge as a
co-occurrence statistic: among plants containing X, which conditions Y come up more
than chance?

IMPORTANT — what this is and isn't:
- It is an ASSOCIATION via the plant, not a causal or mechanistic claim. A plant holds
  dozens of compounds and serves dozens of uses; we cannot attribute an effect to one
  constituent. Results are HYPOTHESIS-GENERATING ("plants with X tend to be used for
  Y"), not "X cures Y".
- Ranking is by a one-sided hypergeometric (Fisher) tail probability, NOT raw lift:
  with few plants per compound, lift inflates wildly on 2-plant coincidences. The
  p-value down-weights small-sample flukes; lift and support are reported alongside so
  the caller can judge strength. Every row carries the supporting plants, so any
  association is auditable back to the grounded monographs.
- The denominator is the population of plants that have BOTH chemistry data and
  target-axis data — so "no co-occurrence" is meaningful (we'd have seen it), and a
  compound isn't penalised for plants we simply have no chemistry on.

A compound query expands to the compound's hierarchy DESCENDANTS (asking about
«флавоноиды» includes plants whose constituent is рутин/кверцетин); an indication query
likewise rolls in descendant concepts, mirroring the retrieval resolver.
"""

import uuid
from collections import Counter, defaultdict
from math import lgamma, exp

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant import (
    Indication,
    MedicinalAction,
    Compound,
    PlantMedicinalUse,
    PlantCompound,
    Plant,
)


def _logcomb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def _hyper_sf(a: int, N: int, K: int, n: int) -> float:
    """One-sided hypergeometric tail P(X ≥ a) for drawing ``n`` plants from ``N`` of
    which ``K`` are 'successes' (treat Y). Computed in log-space (lgamma) so it stays
    fast and stable without scipy and without big-int blowup."""
    lo = max(a, n - (N - K))          # smallest feasible overlap
    hi = min(n, K)                    # largest feasible overlap
    if a > hi:
        return 0.0
    lo = max(lo, a)
    log_den = _logcomb(N, n)
    terms = [_logcomb(K, i) + _logcomb(N - K, n - i) - log_den for i in range(lo, hi + 1)]
    if not terms:
        return 0.0
    m = max(terms)
    return min(1.0, exp(m) * sum(exp(t - m) for t in terms))


def _descendants(rows: list, root_id: uuid.UUID) -> set[uuid.UUID]:
    """All ids in the subtree rooted at ``root_id`` (inclusive), via parent_id BFS over
    the in-memory vocabulary rows."""
    children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for r in rows:
        if r.parent_id:
            children[r.parent_id].append(r.id)
    out = {root_id}
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for c in children.get(cur, ()):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def _rank(
    base: set[uuid.UUID],
    source_plants: set[uuid.UUID],
    plant_targets: dict[uuid.UUID, set[uuid.UUID]],
    names: dict[uuid.UUID, str],
    plant_names: dict[uuid.UUID, dict],
    min_support: int,
    limit: int,
) -> dict:
    """Core co-occurrence rank. ``base`` is the analysable plant population; ``source``
    are the plants carrying the query (compound/indication); ``plant_targets`` maps each
    plant to the target concepts it links. Returns the meta + ranked target rows."""
    src = source_plants & base
    N = len(base)
    n = len(src)
    if n == 0 or N == 0:
        return {"n_base": N, "n_source": n, "results": []}

    K: Counter = Counter()
    for p in base:
        for t in plant_targets.get(p, ()):
            K[t] += 1
    A: Counter = Counter()
    src_targets: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for p in src:
        for t in plant_targets.get(p, ()):
            A[t] += 1
            src_targets[t].append(p)

    rows = []
    for t, a in A.items():
        if a < min_support:
            continue
        Kk = K[t]
        if Kk == 0:
            continue
        lift = (a / n) / (Kk / N)
        p_value = _hyper_sf(a, N, Kk, n)
        supporting = [
            {"id": str(pid), "name": plant_names.get(pid, {}).get("name"),
             "name_latin": plant_names.get(pid, {}).get("name_latin")}
            for pid in src_targets[t][:8]
        ]
        rows.append({
            "id": str(t),
            "name": names.get(t),
            "support": a,                  # plants with source AND target
            "source_plants": n,            # plants with source (in base)
            "target_plants": Kk,           # plants with target (in base)
            "base_plants": N,
            "lift": round(lift, 2),
            "p_value": p_value,
            "plants": supporting,
        })
    rows.sort(key=lambda r: (r["p_value"], -r["lift"]))
    return {"n_base": N, "n_source": n, "results": rows[:limit]}


async def _plant_names(db: AsyncSession) -> dict[uuid.UUID, dict]:
    res = await db.execute(select(Plant.id, Plant.name, Plant.name_latin))
    return {row[0]: {"name": row[1], "name_latin": row[2]} for row in res.all()}


async def compound_associations(
    db: AsyncSession, compound_id: uuid.UUID, axis: str = "indication",
    limit: int = 20, min_support: int = 2,
) -> dict:
    """For a compound (+ its hierarchy descendants), the INDICATIONS or ACTIONS that
    plants containing it tend to be used for, ranked by hypergeometric significance.

    ``axis="action"`` is more robust (fewer, broader categories → more support per cell,
    closer to mechanism); ``axis="indication"`` answers the literal "which conditions"
    question but is sparser. Returns the source concept, population sizes, and ranked
    rows each with lift / support / p_value / supporting plants."""
    comp_rows = (await db.execute(select(Compound))).scalars().all()
    src_comp_ids = _descendants(comp_rows, compound_id)
    source_self = next((c for c in comp_rows if c.id == compound_id), None)
    if source_self is None:
        return {"error": "compound not found"}

    # plant -> compounds (for base membership), and source plants.
    pcs = (await db.execute(select(PlantCompound))).scalars().all()
    plants_with_compound: set[uuid.UUID] = set()
    source_plants: set[uuid.UUID] = set()
    for pc in pcs:
        if pc.compound_id:
            plants_with_compound.add(pc.plant_id)
            if pc.compound_id in src_comp_ids:
                source_plants.add(pc.plant_id)

    uses = (await db.execute(select(PlantMedicinalUse))).scalars().all()
    plant_targets: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    plants_with_target: set[uuid.UUID] = set()
    if axis == "action":
        names = {a.id: a.name for a in (await db.execute(select(MedicinalAction))).scalars()}
        for u in uses:
            if u.action_id:
                plant_targets[u.plant_id].add(u.action_id)
                plants_with_target.add(u.plant_id)
    else:
        axis = "indication"
        names = {i.id: i.name for i in (await db.execute(select(Indication))).scalars()}
        for u in uses:
            for iid in (u.indication_ids or []):
                plant_targets[u.plant_id].add(iid)
                plants_with_target.add(u.plant_id)

    base = plants_with_compound & plants_with_target
    pnames = await _plant_names(db)
    ranked = _rank(base, source_plants, plant_targets, names, pnames, min_support, limit)
    return {
        "source": {"kind": "compound", "id": str(compound_id), "name": source_self.name,
                   "name_latin": source_self.name_latin,
                   "descendants_included": len(src_comp_ids) - 1},
        "axis": axis,
        "min_support": min_support,
        "note": ("Association via the bridging plant, not a causal/mechanistic claim. "
                 "Plants containing this compound tend to be used for the targets below; "
                 "rank by p_value (small = unlikely by chance), read lift/support for "
                 "strength, and verify each via the supporting plants' monographs."),
        **ranked,
    }


async def indication_compounds(
    db: AsyncSession, indication_id: uuid.UUID, limit: int = 20, min_support: int = 2,
) -> dict:
    """Reverse direction: for an indication (+ descendant concepts), the COMPOUNDS most
    over-represented in the plants used to treat it — "what chemistry underlies treating
    Y". Same hypergeometric ranking and caveats as ``compound_associations``."""
    ind_rows = (await db.execute(select(Indication))).scalars().all()
    src_ind_ids = _descendants(ind_rows, indication_id)
    source_self = next((i for i in ind_rows if i.id == indication_id), None)
    if source_self is None:
        return {"error": "indication not found"}

    uses = (await db.execute(select(PlantMedicinalUse))).scalars().all()
    plants_with_indication: set[uuid.UUID] = set()
    source_plants: set[uuid.UUID] = set()
    for u in uses:
        ids = u.indication_ids or []
        if ids:
            plants_with_indication.add(u.plant_id)
            if any(iid in src_ind_ids for iid in ids):
                source_plants.add(u.plant_id)

    pcs = (await db.execute(select(PlantCompound))).scalars().all()
    plant_targets: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    plants_with_compound: set[uuid.UUID] = set()
    for pc in pcs:
        if pc.compound_id:
            plant_targets[pc.plant_id].add(pc.compound_id)
            plants_with_compound.add(pc.plant_id)

    names = {c.id: c.name for c in (await db.execute(select(Compound))).scalars()}
    base = plants_with_compound & plants_with_indication
    pnames = await _plant_names(db)
    ranked = _rank(base, source_plants, plant_targets, names, pnames, min_support, limit)
    return {
        "source": {"kind": "indication", "id": str(indication_id), "name": source_self.name,
                   "name_modern": source_self.name_modern,
                   "descendants_included": len(src_ind_ids) - 1},
        "axis": "compound",
        "min_support": min_support,
        "note": ("Association via the bridging plant, not a causal/mechanistic claim. "
                 "These compounds are over-represented among plants used for this "
                 "condition; rank by p_value, read lift/support for strength, and verify "
                 "via the supporting plants' monographs."),
        **ranked,
    }
