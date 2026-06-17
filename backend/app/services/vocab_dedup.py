"""Merge near-duplicate concepts in a controlled vocabulary — generically.

This generalizes the indication dedup (``medical_dedup``) into a single engine that
also collapses the ACTION (``MedicinalAction``) and COMPOUND (``Compound``)
vocabularies, which the corpus-wide normalizers build just as conservatively and so
leave with the same kind of twins (``мочегонное``/``мочегонное действие``, ё/е
spelling pairs, a row whose headword IS another row's modern name).

The three vocabularies differ in two structural ways the engine abstracts over a
``VocabDedupSpec``:

- **How a use-fact links the concept.** Indications link via an ARRAY
  (``PlantMedicinalUse.indication_ids``); actions and compounds link via a SCALAR FK
  (``PlantMedicinalUse.action_id`` / ``PlantCompound.compound_id``). Repointing an
  array rewrites+dedupes the list; repointing a scalar is a plain ``UPDATE … SET``.
- **Which fields carry surface forms.** Every vocab has ``name`` + ``synonyms``; the
  "secondary canonical name" is ``name_modern`` (indication, action) or ``name_latin``
  (compound); only the indication has an ``archaic`` bridge.

Everything else mirrors ``medical_dedup`` exactly — and on purpose. Clusters form only
from HIGH-CONFIDENCE, exact-normalized, SYMMETRIC canonical-naming signals (shared
modern name, headword==another's modern name, shared ё/е-folded headword); the loose
``archaic`` list is NOT a merge signal (it drives retrieval, not identity). A cluster
mixing a hierarchy parent with its own descendant is left untouched and reported. The
pass is review-first: ``apply=False`` returns the proposed merges without writing.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, delete, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.plant import (
    Indication,
    MedicinalAction,
    Compound,
    PlantMedicinalUse,
    PlantCompound,
)

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _norm(s: str | None) -> str:
    """Lowercase, fold ё→е, drop punctuation, collapse whitespace — so ``отёки`` and
    ``отеки`` normalize equal and exact-match signals are spelling-robust."""
    return " ".join(_WORD_RE.findall((s or "").lower().replace("ё", "е")))


# Sub/superscript digits → ASCII (so «B₁» vs «B₁₂» stay DISTINCT, not both collapsed to
# «B»). 20 source chars ↔ 20 target chars.
_SUBSUP = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")


def _canon_compound(s: str | None) -> str:
    """Chemistry-aware canonical key for compound names. Collapses ONLY typographic
    noise (dash/space/apostrophe/punctuation) while KEEPING the tokens that distinguish
    real compounds: Greek prefixes (α/β/γ/δ), digits, and subscript indices. So
    «виценин-2» / «виценин 2» / «виценин – 2» fold to one, but «α-токоферол» ≠
    «β-токоферол», «α-пинен» ≠ «β-пинен», «витамин B₁» ≠ «витамин B₁₂».

    Uses `str.isalnum()` (Unicode-aware: keeps Cyrillic + Latin + Greek letters + digits,
    drops separators/punctuation) — the generic _norm above instead uses a [а-яa-z0-9]
    regex that DROPS Greek + subscripts and would WRONGLY merge these chemically-distinct
    isomers/vitamers/homologs."""
    s = (s or "").lower().replace("ё", "е").translate(_SUBSUP)
    return "".join(c for c in s if c.isalnum())


@dataclass(frozen=True)
class VocabDedupSpec:
    """Describes one dedup-able vocabulary: its model, the use-fact link, and which
    fields hold surface forms (so the engine can fold duplicates without recall loss).

    ``modern_attr`` is the secondary canonical-name column (``name_modern`` /
    ``name_latin``) used both as a merge signal and folded into ``synonyms`` on apply.
    ``archaic_attr`` is the archaic-bridge list column, or ``None`` for vocabularies
    that have none. ``link_is_array`` selects array-rewrite vs scalar-UPDATE repointing.
    """

    model: type
    label: str                  # human label for messages, e.g. "indication"
    modern_attr: str | None     # name_modern / name_latin / None
    archaic_attr: str | None    # archaic / None
    link_model: type
    link_attr: str              # indication_ids / action_id / compound_id
    link_is_array: bool
    # Name-normalizer used for clustering. Defaults to the generic word-token _norm;
    # the compound vocab overrides it with the chemistry-aware _canon_compound so Greek
    # prefixes / subscripts are NOT stripped (α-pinene must not merge with β-pinene).
    norm_fn: "callable" = _norm


INDICATION_SPEC = VocabDedupSpec(
    model=Indication, label="indication", modern_attr="name_modern",
    archaic_attr="archaic", link_model=PlantMedicinalUse,
    link_attr="indication_ids", link_is_array=True,
)
ACTION_SPEC = VocabDedupSpec(
    model=MedicinalAction, label="action", modern_attr="name_modern",
    archaic_attr=None, link_model=PlantMedicinalUse,
    link_attr="action_id", link_is_array=False,
)
COMPOUND_SPEC = VocabDedupSpec(
    model=Compound, label="compound", modern_attr="name_latin",
    archaic_attr=None, link_model=PlantCompound,
    link_attr="compound_id", link_is_array=False,
    norm_fn=_canon_compound,   # chemistry-aware: keep Greek/subscripts (α-pinene ≠ β-pinene)
)


class _DSU:
    """Tiny union-find over vocabulary ids."""

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


async def _fact_counts(db: AsyncSession, spec: VocabDedupSpec) -> dict[uuid.UUID, int]:
    """How many use-facts link each concept. Array links are unnested then grouped;
    scalar FK links are grouped directly."""
    col = getattr(spec.link_model, spec.link_attr)
    if spec.link_is_array:
        sub = select(func.unnest(col).label("iid")).where(col.isnot(None)).subquery()
        res = await db.execute(select(sub.c.iid, func.count()).group_by(sub.c.iid))
    else:
        res = await db.execute(select(col, func.count()).where(col.isnot(None)).group_by(col))
    return {row[0]: row[1] for row in res.all()}


def _build_clusters(rows: list, spec: VocabDedupSpec) -> list[list]:
    """Group rows denoting the same concept via exact-normalized, SYMMETRIC,
    canonical-naming signals: (A) shared modern name, (B) one row's headword ==
    another's modern name, (C) shared headword (ё/е-folded).

    Deliberately does NOT use archaic-list membership as a merge signal: the
    ``archaic`` bridge is populated loosely, so treating it as proof of identity
    cascades distinct conditions together. Archaic still drives RETRIEVAL; it just
    isn't trusted for MERGES."""
    nf = spec.norm_fn   # per-vocab name normalizer (chemistry-aware for compounds)
    name_norm = {r.id: nf(r.name) for r in rows}
    mod_norm = {
        r.id: nf(getattr(r, spec.modern_attr)) if spec.modern_attr else "" for r in rows
    }

    name_index: dict[str, list[uuid.UUID]] = {}
    mod_index: dict[str, list[uuid.UUID]] = {}
    for r in rows:
        if name_norm[r.id]:
            name_index.setdefault(name_norm[r.id], []).append(r.id)
        if mod_norm[r.id]:
            mod_index.setdefault(mod_norm[r.id], []).append(r.id)

    dsu = _DSU()
    for r in rows:
        dsu.find(r.id)
    # A + C: union rows sharing a normalized modern name / headword.
    for group in list(mod_index.values()) + list(name_index.values()):
        for other in group[1:]:
            dsu.union(group[0], other)
    # B: a row's headword equals another row's modern name (same concept).
    for r in rows:
        nm = name_norm[r.id]
        if not nm:
            continue
        for other in mod_index.get(nm, []):
            dsu.union(r.id, other)

    clusters: dict[uuid.UUID, list] = {}
    for r in rows:
        clusters.setdefault(dsu.find(r.id), []).append(r)
    return [c for c in clusters.values() if len(c) > 1]


def _has_internal_hierarchy(members: list) -> bool:
    """True if any member is the parent of another member — a real parent→child edge,
    not a duplicate, so the cluster is left alone."""
    ids = {m.id for m in members}
    return any(m.parent_id in ids for m in members)


def _pick_canonical(members: list, counts: dict[uuid.UUID, int], spec: VocabDedupSpec):
    """Best-covered wins; ties broken by richer synonym/archaic payload, then by
    having a modern name, then a stable id order."""
    def key(m):
        payload = len(getattr(m, "synonyms", None) or [])
        if spec.archaic_attr:
            payload += len(getattr(m, spec.archaic_attr) or [])
        has_modern = 1 if (spec.modern_attr and getattr(m, spec.modern_attr)) else 0
        return (counts.get(m.id, 0), payload, has_modern, str(m.id))
    return max(members, key=key)


def _preview_member(m, spec: VocabDedupSpec, counts: dict[uuid.UUID, int]) -> dict:
    out = {"id": str(m.id), "name": m.name, "linked_facts": counts.get(m.id, 0)}
    if spec.modern_attr:
        out[spec.modern_attr] = getattr(m, spec.modern_attr)
    return out


async def dedup_vocabulary(db: AsyncSession, spec: VocabDedupSpec, apply: bool = False) -> dict:
    """Find and (optionally) merge duplicate concepts in the vocabulary ``spec`` names.

    ``apply=False`` (default) → dry run: returns the proposed merges for review,
    writes nothing. ``apply=True`` → folds each cluster's duplicates into the
    canonical concept (surface forms preserved as synonyms/archaic), repoints every
    use-fact link (array rewrite or scalar UPDATE) and child ``parent_id``, deletes the
    dups, commits."""
    rows = (await db.execute(select(spec.model))).scalars().all()
    vocab_before = len(rows)
    counts = await _fact_counts(db, spec)

    mergeable: list[tuple] = []
    skipped: list[dict] = []
    for cluster in _build_clusters(rows, spec):
        if _has_internal_hierarchy(cluster):
            skipped.append({
                "reason": "internal_hierarchy",
                "members": [_preview_member(m, spec, counts) for m in cluster],
            })
            continue
        canonical = _pick_canonical(cluster, counts, spec)
        dups = [m for m in cluster if m.id != canonical.id]
        mergeable.append((canonical, dups))

    details = []
    for canonical, dups in mergeable:
        details.append({
            "canonical": _preview_member(canonical, spec, counts),
            "merged": [_preview_member(d, spec, counts) for d in dups],
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
        "vocabulary": spec.label,
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
        seen_syn = {_norm(s) for s in syn}
        arc = list(getattr(canonical, spec.archaic_attr) or []) if spec.archaic_attr else None
        seen_arc = {_norm(a) for a in arc} if arc is not None else None
        for d in dups:
            surface = [d.name]
            if spec.modern_attr:
                surface.append(getattr(d, spec.modern_attr))
            surface.extend(d.synonyms or [])
            for v in surface:
                if v and _norm(v) not in seen_syn:
                    syn.append(v)
                    seen_syn.add(_norm(v))
            if spec.archaic_attr:
                for a in (getattr(d, spec.archaic_attr) or []):
                    if a and _norm(a) not in seen_arc:
                        arc.append(a)
                        seen_arc.add(_norm(a))
            if spec.modern_attr and not getattr(canonical, spec.modern_attr) and getattr(d, spec.modern_attr):
                setattr(canonical, spec.modern_attr, getattr(d, spec.modern_attr))
        canonical.synonyms = syn
        flag_modified(canonical, "synonyms")
        if spec.archaic_attr:
            setattr(canonical, spec.archaic_attr, arc)
            flag_modified(canonical, spec.archaic_attr)

    # 2. Repoint every use-fact link from a duplicate to its canonical.
    uses_repointed = 0
    col = getattr(spec.link_model, spec.link_attr)
    if spec.link_is_array:
        affected = (await db.execute(
            select(spec.link_model).where(
                or_(*[col.any(d) for d in rows_to_delete])
            )
        )).scalars().all()
        for u in affected:
            cur = list(getattr(u, spec.link_attr) or [])
            new_ids: list[uuid.UUID] = []
            for iid in cur:
                mapped = dup_to_canon.get(iid, iid)
                if mapped not in new_ids:
                    new_ids.append(mapped)
            if new_ids != cur:
                setattr(u, spec.link_attr, new_ids)
                uses_repointed += 1
    else:
        for dup_id, canon_id in dup_to_canon.items():
            res = await db.execute(
                update(spec.link_model).where(col == dup_id).values(**{spec.link_attr: canon_id})
            )
            uses_repointed += res.rowcount or 0

    # 3. Repoint children whose parent is a duplicate (avoid self-loops).
    children = (await db.execute(
        select(spec.model).where(spec.model.parent_id.in_(rows_to_delete))
    )).scalars().all()
    for ch in children:
        new_parent = dup_to_canon.get(ch.parent_id)
        ch.parent_id = None if new_parent == ch.id else new_parent

    # 4. Delete the now-orphaned duplicate rows.
    await db.execute(delete(spec.model).where(spec.model.id.in_(rows_to_delete)))
    await db.commit()

    result["vocab_after"] = vocab_before - len(rows_to_delete)
    result["uses_repointed"] = uses_repointed
    return result


async def dedup_indications(db: AsyncSession, apply: bool = False) -> dict:
    """Merge duplicate INDICATION concepts (array link + archaic bridge)."""
    return await dedup_vocabulary(db, INDICATION_SPEC, apply=apply)


async def dedup_actions(db: AsyncSession, apply: bool = False) -> dict:
    """Merge duplicate ACTION concepts (scalar ``action_id`` link, no archaic)."""
    return await dedup_vocabulary(db, ACTION_SPEC, apply=apply)


async def dedup_compounds(db: AsyncSession, apply: bool = False) -> dict:
    """Merge duplicate COMPOUND concepts (scalar ``compound_id`` link, no archaic)."""
    return await dedup_vocabulary(db, COMPOUND_SPEC, apply=apply)
