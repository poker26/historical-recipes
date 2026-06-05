"""Merge near-duplicate concepts in the controlled INDICATION vocabulary.

The corpus-wide normalize (``medical_matching.normalize_medical_uses``) builds the
vocabulary conservatively, which leaves several rows denoting the SAME condition:
``водянка``/``асцит``/``брюшная водянка`` (all modern-name «асцит»), ``понос``/
``поносы`` (both «диарея»), ``отёки``/``отеки`` (ё/е spelling twins), ``чахотка``/
``туберкулёз`` (a row whose headword IS another row's modern name). Retrieval isn't
broken — the ``indication=`` resolver already unions every matching concept + its
children — so this is a DATA-QUALITY pass: collapse the twins into one canonical
concept, **preserving recall** by folding every duplicate's surface forms into the
canonical's ``synonyms``/``archaic`` (the archaic→modern bridge), repoint every
``PlantMedicinalUse.indication_ids`` link, then delete the duplicate rows.

Discipline mirrors the matcher's conservatism: clusters are formed only from
HIGH-CONFIDENCE, exact-normalized signals (a wrong merge collapses two distinct
conditions — worse than leaving a duplicate). A cluster that mixes a hierarchy
parent with its own descendant is left untouched and reported for human review.
The whole pass is review-first: ``apply=False`` returns the proposed merges without
writing; only ``apply=True`` mutates.
"""

import re
import uuid

from sqlalchemy import select, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.plant import Indication, PlantMedicinalUse

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _norm(s: str | None) -> str:
    """Lowercase, fold ё→е, drop punctuation, collapse whitespace — so ``отёки`` and
    ``отеки`` normalize equal and exact-match signals are spelling-robust."""
    return " ".join(_WORD_RE.findall((s or "").lower().replace("ё", "е")))


class _DSU:
    """Tiny union-find over indication ids."""

    def __init__(self):
        self.parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(self, x: uuid.UUID) -> uuid.UUID:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: uuid.UUID, b: uuid.UUID) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


async def _indication_fact_counts(db: AsyncSession) -> dict[uuid.UUID, int]:
    """How many ``PlantMedicinalUse`` link each concept, in one grouped query
    (unnest the ``indication_ids`` arrays and count per id)."""
    sub = (
        select(func.unnest(PlantMedicinalUse.indication_ids).label("iid"))
        .where(PlantMedicinalUse.indication_ids.isnot(None))
        .subquery()
    )
    res = await db.execute(select(sub.c.iid, func.count()).group_by(sub.c.iid))
    return {row[0]: row[1] for row in res.all()}


def _build_clusters(rows: list[Indication]) -> list[list[Indication]]:
    """Group rows that denote the same condition via exact-normalized signals:
    (A) shared modern name, (B) one row's headword == another's modern name,
    (C) shared headword (ё/е-folded), (D) one row's headword listed in another's
    ``archaic`` bridge. All four are high-confidence full-string equalities."""
    by_id = {r.id: r for r in rows}
    name_norm = {r.id: _norm(r.name) for r in rows}
    nmod_norm = {r.id: _norm(r.name_modern) for r in rows}

    name_index: dict[str, list[uuid.UUID]] = {}
    nmod_index: dict[str, list[uuid.UUID]] = {}
    archaic_index: dict[str, list[uuid.UUID]] = {}
    for r in rows:
        if name_norm[r.id]:
            name_index.setdefault(name_norm[r.id], []).append(r.id)
        if nmod_norm[r.id]:
            nmod_index.setdefault(nmod_norm[r.id], []).append(r.id)
        for a in (r.archaic or []):
            na = _norm(a)
            if na:
                archaic_index.setdefault(na, []).append(r.id)

    dsu = _DSU()
    for r in rows:
        dsu.find(r.id)
    # A + C: union rows sharing a normalized modern name / headword.
    for group in list(nmod_index.values()) + list(name_index.values()):
        for other in group[1:]:
            dsu.union(group[0], other)
    # B + D: a row's headword equals another's modern name, or is in its archaic list.
    for r in rows:
        nm = name_norm[r.id]
        if not nm:
            continue
        for other in nmod_index.get(nm, []):
            dsu.union(r.id, other)
        for other in archaic_index.get(nm, []):
            dsu.union(r.id, other)

    clusters: dict[uuid.UUID, list[Indication]] = {}
    for r in rows:
        clusters.setdefault(dsu.find(r.id), []).append(r)
    return [c for c in clusters.values() if len(c) > 1]


def _has_internal_hierarchy(members: list[Indication]) -> bool:
    """True if any member is the parent of another member — that's a real
    parent→child hierarchy edge, not a duplicate, so the cluster is left alone."""
    ids = {m.id for m in members}
    return any(m.parent_id in ids for m in members)


def _pick_canonical(members: list[Indication], counts: dict[uuid.UUID, int]) -> Indication:
    """Best-covered wins; ties broken by richer synonym/archaic payload, then by
    having a modern name, then a stable id order."""
    def key(m: Indication):
        payload = len(m.synonyms or []) + len(m.archaic or [])
        return (counts.get(m.id, 0), payload, 1 if m.name_modern else 0, str(m.id))
    return max(members, key=key)


async def dedup_indications(db: AsyncSession, apply: bool = False) -> dict:
    """Find and (optionally) merge duplicate indication concepts.

    ``apply=False`` (default) → dry run: returns the proposed merges for review,
    writes nothing. ``apply=True`` → folds each cluster's duplicates into the
    canonical concept (surface forms preserved as synonyms/archaic), repoints every
    ``PlantMedicinalUse.indication_ids`` and child ``parent_id``, deletes the dups,
    commits."""
    rows = (await db.execute(select(Indication))).scalars().all()
    vocab_before = len(rows)
    counts = await _indication_fact_counts(db)

    mergeable: list[tuple[Indication, list[Indication]]] = []
    skipped: list[dict] = []
    for cluster in _build_clusters(rows):
        if _has_internal_hierarchy(cluster):
            skipped.append({
                "reason": "internal_hierarchy",
                "members": [{"id": str(m.id), "name": m.name,
                             "name_modern": m.name_modern} for m in cluster],
            })
            continue
        canonical = _pick_canonical(cluster, counts)
        dups = [m for m in cluster if m.id != canonical.id]
        mergeable.append((canonical, dups))

    # Build the human-review preview (sorted by how many facts the cluster carries).
    details = []
    for canonical, dups in mergeable:
        details.append({
            "canonical": {"id": str(canonical.id), "name": canonical.name,
                          "name_modern": canonical.name_modern,
                          "linked_facts": counts.get(canonical.id, 0)},
            "merged": [{"id": str(d.id), "name": d.name, "name_modern": d.name_modern,
                        "linked_facts": counts.get(d.id, 0)} for d in dups],
        })
    details.sort(
        key=lambda d: d["canonical"]["linked_facts"] + sum(m["linked_facts"] for m in d["merged"]),
        reverse=True,
    )

    dup_to_canon: dict[uuid.UUID, uuid.UUID] = {
        d.id: canonical.id for canonical, dups in mergeable for d in dups
    }
    rows_to_delete = list(dup_to_canon.keys())

    result = {
        "applied": apply,
        "vocab_before": vocab_before,
        "clusters_mergeable": len(mergeable),
        "rows_to_delete": len(rows_to_delete),
        "clusters_skipped": len(skipped),
        "skipped": skipped,
        "merges": details,
    }

    if not apply or not mergeable:
        result["vocab_after"] = vocab_before
        result["uses_repointed"] = 0
        return result

    # 1. Fold each duplicate's surface forms into its canonical (recall preserved).
    for canonical, dups in mergeable:
        syn = list(canonical.synonyms or [])
        arc = list(canonical.archaic or [])
        seen_syn = {_norm(s) for s in syn}
        seen_arc = {_norm(a) for a in arc}
        for d in dups:
            for v in [d.name, d.name_modern, *(d.synonyms or [])]:
                if v and _norm(v) not in seen_syn:
                    syn.append(v)
                    seen_syn.add(_norm(v))
            for a in (d.archaic or []):
                if a and _norm(a) not in seen_arc:
                    arc.append(a)
                    seen_arc.add(_norm(a))
            if not canonical.name_modern and d.name_modern:
                canonical.name_modern = d.name_modern
        canonical.synonyms = syn
        canonical.archaic = arc
        flag_modified(canonical, "synonyms")
        flag_modified(canonical, "archaic")

    # 2. Repoint every medicinal-use link from a duplicate to its canonical.
    uses_repointed = 0
    affected = (await db.execute(
        select(PlantMedicinalUse).where(
            or_(*[PlantMedicinalUse.indication_ids.any(d) for d in rows_to_delete])
        )
    )).scalars().all()
    for u in affected:
        new_ids: list[uuid.UUID] = []
        for iid in (u.indication_ids or []):
            mapped = dup_to_canon.get(iid, iid)
            if mapped not in new_ids:
                new_ids.append(mapped)
        if new_ids != list(u.indication_ids or []):
            u.indication_ids = new_ids
            uses_repointed += 1

    # 3. Repoint children whose parent is a duplicate (avoid self-loops).
    children = (await db.execute(
        select(Indication).where(Indication.parent_id.in_(rows_to_delete))
    )).scalars().all()
    for ch in children:
        new_parent = dup_to_canon.get(ch.parent_id)
        ch.parent_id = None if new_parent == ch.id else new_parent

    # 4. Delete the now-orphaned duplicate rows.
    await db.execute(delete(Indication).where(Indication.id.in_(rows_to_delete)))
    await db.commit()

    result["vocab_after"] = vocab_before - len(rows_to_delete)
    result["uses_repointed"] = uses_repointed
    return result
