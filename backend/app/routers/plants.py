import re
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.services.compound_normalize import compound_merge_key
from app.models.book import Book
from app.models.recipe import Recipe, RecipeIngredient
from app.models.plant import (
    Plant,
    MedicinalAction,
    Indication,
    PlantMedicinalUse,
    PlantCompound,
    PlantHarvest,
    PlantHabitat,
    PlantBiotope,
    PlantToxicity,
    PlantCulinaryUse,
    PlantBookMention,
    EssentialOil,
    EssentialOilUse,
)
from app.models.reader_monograph import PlantReaderMonograph
from app.services.biotope import BIOTOPE_GROUP
from app.services.plant_matching import relink_recipe_ingredients, merge_plants_by_latin_key
from app.services.compound_matching import is_nontarget_compound_class
from app.services.medical_matching import is_nontarget_action
from app.services.qdrant import delete_points
from app.services.inaturalist import enrich_plants_inat, find_observations

router = APIRouter()

QDRANT_PLANTS_COLLECTION = "plants_v2"


@router.post("/relink-recipes")
async def relink_recipes(db: AsyncSession = Depends(get_db)):
    """Backfill recipe↔plant links across the whole corpus.

    Recipe books processed before any herbalism book have NULL plant links
    (the plants table was empty at match time). This re-runs the normalized,
    alt-name-aware matcher over every ingredient now that plants exist.
    """
    result = await relink_recipe_ingredients(db)
    return {"status": "completed", **result}


@router.post("/dedupe-latin")
async def dedupe_latin(dry_run: bool = True, db: AsyncSession = Depends(get_db)):
    """Merge herbarium duplicates that share a latin binomial (genus + species).

    A plant can end up as several rows — a recipe book makes a stub, a determiner
    later adds the full monograph under "Genus species L." — whose latin names
    agree once the author citation and case are ignored. This folds each such
    group into its richest row, repointing all facts and recipe links.

    Defaults to ``dry_run`` (returns the plan, writes nothing) so the scale can
    be reviewed; pass ``?dry_run=false`` to execute. After a real merge the
    losing rows' ``plants_v2`` points are purged so search has no stale ghosts.
    """
    result = await merge_plants_by_latin_key(db, dry_run=dry_run)
    if not dry_run and result["deleted_qdrant_ids"]:
        await delete_points(QDRANT_PLANTS_COLLECTION, result["deleted_qdrant_ids"])
    return {"status": "completed", **result}


@router.post("/enrich-inat")
async def enrich_inat(
    dry_run: bool = True,
    limit: int = 150,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Enrich the herbarium from iNaturalist: resolve each plant's latin name to
    an iNat taxon and store a license-clean canonical photo (CC0/CC-BY/CC-BY-SA
    only, attribution kept).

    Idempotent & resumable — only touches not-yet-synced plants unless
    ``force=true``. ``limit`` bounds one call (paced ~1 req/s for iNat's rate
    limit) so it stays under the proxy timeout; re-run until ``remaining`` is 0.
    Defaults to ``dry_run`` (returns the plan, writes nothing)."""
    result = await enrich_plants_inat(db, dry_run=dry_run, limit=limit, force=force)
    return {"status": "completed", **result}


@router.post("/enrich-inat/run")
async def run_enrich_inat():
    """Start the durable corpus-wide iNat enrichment sweep (Temporal).

    The robust replacement for hammering ``/enrich-inat`` in a shell loop: one
    ``InatEnrichmentWorkflow`` paces through every unsynced plant in batches,
    retries 429s, and resumes after a worker restart. Rejects a second start
    while one is already running."""
    from temporalio.service import RPCError
    from app.config import settings
    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import InatEnrichmentWorkflow

    client = await get_temporal_client()
    wf_id = "inat-enrichment"
    try:
        desc = await client.get_workflow_handle(wf_id).describe()
        if desc.status and desc.status.name == "RUNNING":
            raise HTTPException(status_code=409, detail="iNat enrichment already running")
    except RPCError:
        pass  # no existing workflow — fine

    handle = await client.start_workflow(
        InatEnrichmentWorkflow.run,
        args=[],
        id=wf_id,
        task_queue=settings.temporal_task_queue,
    )
    return {"status": "started", "workflow_id": handle.id, "run_id": handle.result_run_id}


def _plant_summary(p: Plant, uses_count: int = 0) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "name_latin": p.name_latin,
        "name_modern": p.name_modern,
        "names_historical": p.names_historical,
        "family": p.family,
        "family_latin": p.family_latin,
        "parts_used": p.parts_used,
        "is_toxic": p.is_toxic,
        "kingdom": p.kingdom,
        "photo_url": p.photo_url,
        "photo_attribution": p.photo_attribution,
        "uses_count": uses_count,
        # genus tier: a list card may be a genus HUB — clients branch on rank.
        "rank": p.rank,
        # forager-safety badge for list cards (RFC-edible-safety)
        "safety_level": p.safety_level,
        "deadly_twin": p.deadly_twin,
    }


# Forager-safety (RFC-edible-safety): ordinal «can I eat this» verdict.
SAFETY_LABELS = {
    0: "нет данных",
    1: "съедобно",
    2: "условно съедобно",
    3: "опасно — лекарственное, дозозависимо",
    4: "смертельно ядовито",
}


def _safety_block(p) -> dict | None:
    """The «не умру ли я, если съем» answer — shown as the card's FIRST block.
    None until classified (safety_level NULL = not yet processed)."""
    lvl = getattr(p, "safety_level", None)
    if lvl is None:
        return None
    return {
        "level": lvl,
        "label": SAFETY_LABELS.get(lvl, "нет данных"),
        "edible_parts": p.edible_parts or [],
        "dangerous_parts": p.dangerous_parts or [],
        "deadly_twin": p.deadly_twin,
        "rationale": p.safety_rationale,
    }


@router.get("/")
async def list_plants(
    response: Response,
    q: str | None = None,
    compound: str | None = None,
    action: str | None = None,
    indication: str | None = None,
    family: str | None = None,
    is_toxic: bool | None = None,
    edibility: str | None = None,
    edible: bool | None = None,
    kingdom: str | None = None,
    biotope: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List plants, optionally filtered by free text and/or structured facets.

    All filters combine with AND. ``compound``/``action``/``indication`` match
    against the plant's child fact rows via EXISTS (no row duplication). The
    ``action`` filter matches BOTH the normalized vocabulary (``action_id`` →
    MedicinalAction) and the verbatim ``action_raw``, since only ~44% of uses
    are normalized but ~97% carry a raw action term.

    Pagination: pass ``limit``/``offset`` to fetch one page; the full filtered
    count is always returned in the ``X-Total-Count`` header. Omitting ``limit``
    returns every match (the historical behaviour the MCP tools rely on).
    """
    # Count medicinal uses per plant so the herbarium grid can show how rich
    # each card is without a second round-trip.
    uses_subq = (
        select(PlantMedicinalUse.plant_id, func.count().label("n"))
        .group_by(PlantMedicinalUse.plant_id)
        .subquery()
    )
    stmt = (
        select(Plant, func.coalesce(uses_subq.c.n, 0))
        .outerjoin(uses_subq, uses_subq.c.plant_id == Plant.id)
        .order_by(Plant.name)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Plant.name.ilike(like),
                Plant.name_modern.ilike(like),
                Plant.name_latin.ilike(like),
                # ARRAY column: match any historical name
                func.array_to_string(Plant.names_historical, " ").ilike(like),
            )
        )
    if compound:
        like = f"%{compound.strip()}%"
        stmt = stmt.where(
            Plant.compounds.any(
                or_(
                    PlantCompound.compound.ilike(like),
                    PlantCompound.compound_group.ilike(like),
                )
            )
        )
    if action:
        like = f"%{action.strip()}%"
        # Resolve to vocabulary terms (name / modern / synonyms), then expand to
        # the matched terms' hierarchy descendants (a group like "действие на ЖКТ"
        # also pulls "вяжущее"). Keep the verbatim action_raw fallback.
        matched_actions = (await db.execute(
            select(MedicinalAction.id).where(
                or_(
                    MedicinalAction.name.ilike(like),
                    MedicinalAction.name_modern.ilike(like),
                    func.array_to_string(MedicinalAction.synonyms, " ").ilike(like),
                )
            )
        )).scalars().all()
        action_id_set = set(matched_actions)
        if action_id_set:
            descendants = (await db.execute(
                select(MedicinalAction.id).where(MedicinalAction.parent_id.in_(action_id_set))
            )).scalars().all()
            action_id_set.update(descendants)
        stmt = stmt.where(
            Plant.medicinal_uses.any(
                or_(
                    PlantMedicinalUse.canon_action.ilike(like),   # canonical (synonyms merged)
                    PlantMedicinalUse.action_raw.ilike(like),
                    PlantMedicinalUse.action_id.in_(action_id_set),
                )
            )
        )
    if indication:
        like = f"%{indication.strip()}%"
        # Resolve the query against the controlled vocabulary so a modern OR an
        # archaic term ("водянка") both reach the same concept, then expand to the
        # concept's children. Match the normalized indication_ids on either, AND
        # keep the verbatim free-text fallback for uses not yet normalized.
        matched = (await db.execute(
            select(Indication.id, Indication.parent_id).where(
                or_(
                    Indication.name.ilike(like),
                    Indication.name_modern.ilike(like),
                    func.array_to_string(Indication.synonyms, " ").ilike(like),
                    func.array_to_string(Indication.archaic, " ").ilike(like),
                )
            )
        )).all()
        concept_ids = {iid for iid, _ in matched}
        if concept_ids:
            children = (await db.execute(
                select(Indication.id).where(Indication.parent_id.in_(concept_ids))
            )).scalars().all()
            concept_ids.update(children)
        preds = [PlantMedicinalUse.indications.ilike(like)]
        preds += [PlantMedicinalUse.indication_ids.any(cid) for cid in concept_ids]
        stmt = stmt.where(Plant.medicinal_uses.any(or_(*preds)))
    if family:
        like = f"%{family.strip()}%"
        stmt = stmt.where(or_(Plant.family.ilike(like), Plant.family_latin.ilike(like)))
    if is_toxic is not None:
        stmt = stmt.where(Plant.is_toxic.is_(is_toxic))
    if kingdom:
        # Exact match on the kingdom tag (растение | гриб). Omit to get both; the
        # catalogue passes kingdom=растение to keep the plant view fungi-free.
        stmt = stmt.where(Plant.kingdom == kingdom.strip())
    if edibility:
        like = f"%{edibility.strip()}%"
        stmt = stmt.where(Plant.culinary_uses.any(PlantCulinaryUse.edibility.ilike(like)))
    if edible is not None:
        # "edible" = has any culinary fact flagged съедобно / условно-съедобно.
        edible_pred = Plant.culinary_uses.any(
            PlantCulinaryUse.edibility.in_(["съедобно", "условно-съедобно"])
        )
        stmt = stmt.where(edible_pred if edible else ~edible_pred)
    if biotope:
        # Reverse browse: plants tagged with a canonical biotope ("что растёт на лугу").
        stmt = stmt.where(Plant.biotopes.any(PlantBiotope.biotope == biotope.strip()))

    # Total matching rows (before pagination) → header for the herbarium UI.
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    rows = (await db.execute(stmt)).all()
    return [_plant_summary(p, n) for p, n in rows]


@router.get("/biotopes")
async def biotope_vocabulary(db: AsyncSession = Depends(get_db)):
    """The controlled biotope vocabulary (chips for the «где искать» filter) +
    a live plant count per biotope. Use ``GET /api/plants?biotope=<key>`` to list."""
    from app.services.biotope import BIOTOPES, BIOTOPE_GROUP
    counts = dict((await db.execute(
        select(PlantBiotope.biotope, func.count(func.distinct(PlantBiotope.plant_id)))
        .where(PlantBiotope.biotope.isnot(None)).group_by(PlantBiotope.biotope))).all())
    return {"biotopes": [
        {"key": b, "group": BIOTOPE_GROUP.get(b, "прочее"), "count": counts.get(b, 0)}
        for b in BIOTOPES]}


@router.get("/facets")
async def plant_facets(db: AsyncSession = Depends(get_db)):
    """Distinct filter options for the herbarium UI, each with a plant count.

    ``compound_groups`` are the normalized constituent groups; ``actions`` are
    the normalized medicinal-action vocabulary terms actually in use. Counts are
    distinct plants, so they read as "N plants have this".
    """
    group_count = func.count(func.distinct(PlantCompound.plant_id))
    groups = (await db.execute(
        select(PlantCompound.compound_group, group_count)
        .where(PlantCompound.compound_group.isnot(None))
        .group_by(PlantCompound.compound_group)
        .order_by(group_count.desc())
    )).all()

    # Canonical action facet (action_normalize): ~60 controlled actions over BOTH the
    # action_id-linked AND the raw uses, synonyms merged — replaces the granular vocab.
    action_count = func.count(func.distinct(PlantMedicinalUse.plant_id))
    actions = (await db.execute(
        select(PlantMedicinalUse.canon_action, action_count)
        .where(PlantMedicinalUse.canon_action.isnot(None))
        .group_by(PlantMedicinalUse.canon_action)
        .order_by(action_count.desc())
    )).all()

    edib_count = func.count(func.distinct(PlantCulinaryUse.plant_id))
    edibilities = (await db.execute(
        select(PlantCulinaryUse.edibility, edib_count)
        .where(PlantCulinaryUse.edibility.isnot(None))
        .group_by(PlantCulinaryUse.edibility)
        .order_by(edib_count.desc())
    )).all()

    kingdom_count = func.count(Plant.id)
    kingdoms = (await db.execute(
        select(Plant.kingdom, kingdom_count)
        .group_by(Plant.kingdom)
        .order_by(kingdom_count.desc())
    )).all()

    return {
        "compound_groups": [{"value": g, "count": n} for g, n in groups],
        "actions": [{"value": a, "count": n} for a, n in actions],
        "edibility": [{"value": e, "count": n} for e, n in edibilities],
        "kingdom": [{"value": k, "count": n} for k, n in kingdoms],
    }


def _book_title_map(books: list[Book]) -> dict[str, str]:
    return {str(b.id): b.title for b in books}


# ── Field-view aggregation helpers (docs/HANDOFF-monograph-aggregation.md) ──────
# Pure, deterministic, no-LLM compaction of one already-loaded plant's relations.

_PHRASE_SEPARATORS = set(";,/\n")

# A single classifier code: optional leading letter (Latin *or* Cyrillic homoglyph,
# e.g. ICD-10 "K25" vs Cyrillic "К25"), 1–3 digits, optional ".\d+", optional range.
_CODE = r"[A-ZА-Я]?\d{1,3}(?:\.\d+)?"
# A trailing/inline parenthetical whose contents are ONLY codes (and separators):
# "(K25, K26, K28)", "(К50-К52)", "(J00-J47, J80-J99)" — but NOT "(2-3 раза)".
_CODE_PARENS = re.compile(
    rf"\s*\(\s*(?:{_CODE}(?:\s*[–-]\s*{_CODE})?\s*[,;]?\s*)+\)"
)
# A token that is nothing but a code / range / punctuation (defence in depth for
# fragments that escaped paren-aware splitting, e.g. "К26", "К28)").
_ONLY_CODE = re.compile(
    rf"^[\s,;()]*(?:{_CODE}(?:\s*[–-]\s*{_CODE})?[\s,;()]*)+$"
)
# Action enumeration separators: commas, semicolons, and a standalone Russian "и".
_ACTION_SPLIT = re.compile(r"\s*[;,]\s*|\s+и\s+")


def _split_phrases(text: str | None) -> list[str]:
    """Split a free-text field (indications, contraindications, symptoms) into
    trimmed phrases. Paren-aware: never splits inside (...) / [...] so a code
    list like "(K25, K26, K28)" stays glued to its concept instead of being torn
    into bare-code fragments. Empty input → empty list."""
    if not text:
        return []
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in _PHRASE_SEPARATORS and depth == 0:
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf = []
        else:
            buf.append(ch)
    seg = "".join(buf).strip()
    if seg:
        out.append(seg)
    return out


def _clean_indication(phrase: str) -> str | None:
    """Strip leaked classifier codes from an indication label. Removes any
    parenthetical that is only codes ("язвенная болезнь (К25, К26, К28)" →
    "язвенная болезнь") and drops a token that is nothing but a code. Returns
    None when nothing readable remains."""
    p = _CODE_PARENS.sub(" ", phrase)
    p = re.sub(r"\s{2,}", " ", p).strip(" ,;()")
    if not p or _ONLY_CODE.match(p):
        return None
    return p


def _split_actions(action_name: str | None, action_raw: str | None) -> list[str]:
    """One medicinal action per fact. A controlled-vocab ``action.name`` is
    already single-valued — use it as-is. A free-text ``action_raw`` may be a
    comma/"и"-joined blob ("диуретический, анальгетический, противовоспалительный")
    — split it so each action ranks and dedups on its own."""
    if action_name and action_name.strip():
        return [action_name.strip()]
    if not action_raw or not action_raw.strip():
        return []
    return [a.strip() for a in _ACTION_SPLIT.split(action_raw) if a.strip()]


def _trim(text: str, limit: int = 240) -> str:
    """Trim a quote to ~limit chars on a word boundary, adding an ellipsis."""
    text = " ".join(text.split())  # collapse internal whitespace/newlines
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return (cut or text[:limit]) + "…"


def _distinct(items) -> list:
    """Order-preserving de-duplication (case-insensitive for strings)."""
    seen: set = set()
    out: list = []
    for it in items:
        if it is None:
            continue
        key = it.strip().lower() if isinstance(it, str) else it
        if isinstance(it, str) and not it.strip():
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(it.strip() if isinstance(it, str) else it)
    return out


_GENERIC_IND_RE = re.compile(r"\b(средств\w*|препарат\w*|действие|применени\w*)\b", re.IGNORECASE)


def _ind_stems(s: str | None) -> frozenset[str]:
    """Crude Russian word-stems (first 5 chars of each content word ≥3 long)."""
    return frozenset(w[:5] for w in re.findall(r"[а-яё]+", (s or "").lower()) if len(w) >= 3)


def _is_circular_indication(indication: str, action: str) -> bool:
    """A circular indication merely restates its action and carries no information
    («успокаивающее» → «успокаивающие средства», «регулирующее месячные» → «регулируют
    месячные»). True when the indication's content stems add nothing beyond the action
    (after stripping generic «средства/действие/применение» words). A specific
    indication (бессонница, раны, импотенция) survives."""
    iw = _ind_stems(_GENERIC_IND_RE.sub(" ", indication))
    return bool(iw) and iw <= _ind_stems(action)


def _field_view(plant, src, recipes: list[dict]) -> dict:
    """Compact, deduped + ranked monograph for the low-bandwidth field client.

    Replaces the raw medicinal_uses / compounds / toxicities / culinary / harvest
    arrays with bounded, consensus-ranked aggregates. Identity fields mirror the
    default view. Every block is nullable/optional so the client omits empties."""

    # ── roles (verdict badges) ──────────────────────────────────────────────
    roles: list[str] = []
    if plant.medicinal_uses:
        roles.append("medicinal")
    if plant.culinary_uses:
        roles.append("edible")
    if plant.is_toxic:
        roles.append("toxic")

    # ── uses: dedup by action key, merge parts/indications, rank by consensus ─
    use_groups: dict[str, dict] = {}
    for u in plant.medicinal_uses:
        # Canonical action (action_normalize) when present — clean, synonyms merged,
        # route/meta already dropped. Else fall back: controlled-vocab single value, or a
        # free-text blob ("a, b и c") split into separate (action, no-id) facts.
        if u.canon_action and u.canon_action.strip():
            row_actions = [(u.canon_action.strip(), None)]
        elif u.action and u.action.name and u.action.name.strip():
            row_actions = [(u.action.name.strip(), str(u.action_id) if u.action_id else None)]
        else:
            row_actions = [(a, None) for a in _split_actions(None, u.action_raw)]
        # Drop route/form/meta non-actions («лечебное», «наружное применение»…) —
        # they bloat the action list; the indication (if any) stays in the DB.
        row_actions = [(a, aid) for (a, aid) in row_actions if not is_nontarget_action(a)]
        # clean ICD/MKB codes out of the indications once per row (shared by every
        # action split out of this row)
        row_indications = [
            cl for ph in _split_phrases(u.indications)
            if (cl := _clean_indication(ph))
        ]
        row_indication_ids = [str(i) for i in (u.indication_ids or [])]
        otext = (u.original_text or "").strip()
        quote_cand = None
        if otext:
            quote_cand = {
                "text": otext,
                "source": src(u.source_book_id),
                "actionable": bool(
                    (u.preparation and u.preparation.strip())
                    or (u.dosage and u.dosage.strip())
                ),
            }
        for action, aid in row_actions:
            key = action.lower()
            g = use_groups.get(key)
            if g is None:
                g = {
                    "action": action,
                    "action_id": None,
                    "_parts": [],
                    "_indications": [],
                    "_indication_ids": [],
                    "_quotes": [],
                    "source_count": 0,
                    "_max_conf": 0.0,
                }
                use_groups[key] = g
            if aid and not g["action_id"]:
                g["action_id"] = aid
            g["source_count"] += 1
            if u.confidence is not None and u.confidence > g["_max_conf"]:
                g["_max_conf"] = u.confidence
            if u.part:
                g["_parts"].append(u.part)
            g["_indications"].extend(row_indications)
            g["_indication_ids"].extend(row_indication_ids)
            if quote_cand:
                g["_quotes"].append(quote_cand)

    uses: list[dict] = []
    for g in use_groups.values():
        ind_counts = Counter(p.lower() for p in g["_indications"])
        # keep the most-frequent distinct indications, cap ~6, preserve casing
        seen_ind: set = set()
        ranked_ind: list[str] = []
        for phrase in sorted(g["_indications"], key=lambda p: (-ind_counts[p.lower()], p)):
            lk = phrase.lower()
            if lk in seen_ind:
                continue
            if _is_circular_indication(phrase, g["action"]):
                continue  # drop indications that merely restate the action
            seen_ind.add(lk)
            ranked_ind.append(phrase)
            if len(ranked_ind) >= 6:
                break
        # All source quotes backing this action, ranked so [0] is the
        # representative one (decision #3): prefer an actionable row (has
        # preparation/dosage), then a cited one, then a moderate length. Every
        # quote is code-stripped + trimmed here so the «читать ещё» expansion
        # never has to round-trip the (deliberately raw) default endpoint
        # (RFC-readmore-quotes-codes).
        quotes: list[dict] = []
        seen_q: set = set()
        for cand in sorted(g["_quotes"], key=lambda q: (
            not q["actionable"],
            q["source"] is None,
            abs(len(q["text"]) - 160),
        )):
            # strip leaked ICD/MKB codes from the quote too (same class as Issue 1)
            clean_text = _trim(_CODE_PARENS.sub(" ", cand["text"]), 240)
            if not clean_text:
                continue
            dk = clean_text.lower()
            if dk in seen_q:
                continue
            seen_q.add(dk)
            quotes.append({"text": clean_text, "source": cand["source"]})
        uses.append({
            "action": g["action"],
            "action_id": g["action_id"],
            "action_definition": None,   # null until the actions vocabulary (RFC A1) lands
            "parts": _distinct(g["_parts"]),
            "indications": ranked_ind,
            "indication_ids": _distinct(g["_indication_ids"]),
            "source_count": g["source_count"],
            # `quotes[0]` is the representative quote; the rest power «читать ещё».
            "quotes": quotes,
            # back-compat alias for clients still reading the single quote.
            "quote": quotes[0] if quotes else None,
        })
    uses.sort(key=lambda x: (-x["source_count"], -use_groups[x["action"].lower()]["_max_conf"]))

    # ── cautions: structured safety, never only in prose ────────────────────
    contraindications: list[str] = []
    for u in plant.medicinal_uses:
        contraindications.extend(_split_phrases(u.contraindications))
    toxic_parts: list[str] = []
    tox_symptoms: list[str] = []
    antidotes: list[str] = []
    for t in plant.toxicities:
        toxic_parts.extend(t.toxic_parts or [])
        tox_symptoms.extend(_split_phrases(t.symptoms))
        if t.antidote and t.antidote.strip():
            antidotes.append(t.antidote.strip())
    cautions = {
        "contraindications": _distinct(contraindications),
        "toxic_parts": _distinct(toxic_parts),
        "symptoms": ", ".join(_distinct(tox_symptoms)) or None,
        "antidote": "; ".join(_distinct(antidotes)) or None,
    }
    if not any([cautions["contraindications"], cautions["toxic_parts"],
                cautions["symptoms"], cautions["antidote"]]):
        cautions = None

    # ── compound_groups: group by compound_group, examples capped, ranked ───
    comp_groups: dict = {}
    for c in plant.compounds:
        # Suppress non-constituent classes (enzymes/synthetics/hormones) from the
        # displayed chemical composition — they're substances named in the source but
        # not what the plant "contains" in a herbal sense. Vitamins/minerals stay.
        if c.compound_ref and is_nontarget_compound_class(c.compound_ref.compound_class):
            continue
        gname = (c.compound_group or "").strip() or None
        g = comp_groups.get(gname)
        if g is None:
            g = {"group": gname, "_examples": {}, "_rows": 0}
            comp_groups[gname] = g
        g["_rows"] += 1
        name = (c.compound or "").strip()
        if not name:
            continue
        # A bare "содержит флавоноиды" yields an example identical to the group
        # label — redundant. Skip it: the group label alone is the content.
        if gname and name.lower() == gname.lower():
            continue
        ex = g["_examples"].get(name.lower())
        cid = str(c.compound_id) if c.compound_id else None
        defn = None
        if c.compound_ref and c.compound_ref.definition and c.compound_ref.definition.strip():
            defn = c.compound_ref.definition.strip()
        if ex is None:
            g["_examples"][name.lower()] = {"name": name, "compound_id": cid, "definition": defn}
        else:
            if ex["compound_id"] is None and cid:
                ex["compound_id"] = cid  # prefer the tappable variant
            if ex.get("definition") is None and defn:
                ex["definition"] = defn
    compound_groups: list[dict] = []
    for g in comp_groups.values():
        named = list(g["_examples"].values())
        # prefer examples with a compound_id (stay tappable), then cap ~6
        named.sort(key=lambda e: (e["compound_id"] is None,))
        shown = named[:6]
        obj: dict = {"group": g["group"]}
        if shown:
            obj["examples"] = shown
        # `count` is a "ещё N" hint only — emit it solely when distinct named
        # compounds exceed what we show, so a 1-compound group never prints a
        # stray "1" or a label repeated as its own example.
        if len(named) > len(shown):
            obj["count"] = len(named)
        obj["_order"] = g["_rows"]
        compound_groups.append(obj)
    # order by group size desc; null ("прочие") group sinks to the end
    compound_groups.sort(key=lambda x: (x["group"] is None, -x["_order"]))
    for obj in compound_groups:
        obj.pop("_order", None)

    # ── harvest: distinct merge of harvests + habitat biotopes ──────────────
    harvest = {
        "parts": _distinct([h.part for h in plant.harvests]),
        "seasons": _distinct([h.season for h in plant.harvests]),
        "where": _distinct([h.biotope for h in plant.habitats]),
    }
    if not any(harvest.values()):
        harvest = None

    # ── habitat: canonical biotope chips (grouped) + geographic regions ──────
    # biotopes = normalized PlantBiotope tags (deterministic — served NOW, no
    # Layer-2 gate; the prose `summary` is added only in the reviewed monograph).
    _biotopes = _distinct([b.biotope for b in plant.biotopes if b.biotope])
    _regions = _distinct([h.region for h in plant.habitats])
    habitat = None
    if _biotopes or _regions:
        habitat = {
            "biotopes": [{"key": b, "group": BIOTOPE_GROUP.get(b, "прочее")} for b in _biotopes],
            "regions": _regions,
        }

    # ── culinary: compacted edible use ──────────────────────────────────────
    culinary = [
        {
            "use": cu.use,
            "part": cu.part,
            "season": cu.season,
            "caution": cu.caution,
        }
        for cu in plant.culinary_uses
        if cu.use or cu.part
    ]

    # ── recipes: tiny refs, cap ~20 + total ─────────────────────────────────
    recipe_refs = [
        {
            "id": r["id"],
            "label": _recipe_label(r),
        }
        for r in recipes
    ]
    recipes_total = len(recipe_refs)
    recipe_refs = recipe_refs[:20]

    # ── sources: distinct book citations across all evidence ────────────────
    source_set: list[str] = []
    for u in plant.medicinal_uses:
        source_set.append(src(u.source_book_id))
    for c in plant.compounds:
        source_set.append(src(c.source_book_id))
    for h in plant.harvests:
        source_set.append(src(h.source_book_id))
    for h in plant.habitats:
        source_set.append(src(h.source_book_id))
    for t in plant.toxicities:
        source_set.append(src(t.source_book_id))
    for cu in plant.culinary_uses:
        source_set.append(src(cu.source_book_id))
    sources = _distinct([s for s in source_set if s])

    return {
        "id": str(plant.id),
        "name": plant.name,
        "name_modern": plant.name_modern,
        "name_latin": plant.name_latin,
        "family": plant.family,
        "family_latin": plant.family_latin,
        "kingdom": plant.kingdom,
        "is_toxic": plant.is_toxic,
        "photo_url": plant.photo_url,
        "photo_attribution": plant.photo_attribution,
        "photo_license": plant.photo_license,
        "photo_source": plant.photo_source,
        "description": plant.description,
        "parts_used": plant.parts_used,
        "roles": roles,
        # «Можно ли это есть» — the forager's first question; render FIRST.
        "safety": _safety_block(plant),
        "fun_fact": None,            # null until the grounded «Интересное» field (RFC A3) lands
        "uses": uses,
        "cautions": cautions,
        "compound_groups": compound_groups,
        "harvest": harvest,
        "habitat": habitat,
        "culinary": culinary,
        "recipes": recipe_refs,
        "recipes_total": recipes_total,
        "sources": sources,
    }


def _recipe_label(r: dict) -> str:
    """Compact "Name — Book Year" label for a recipe ref."""
    label = r.get("name") or "—"
    book = r.get("book")
    year = r.get("year")
    if book:
        label = f"{label} — {book}" + (f" {year}" if year else "")
    return label


@router.get("/{plant_id}/observations")
async def plant_observations(
    plant_id: uuid.UUID,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 50.0,
    place: str | None = None,
    place_id: int | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Live iNaturalist "where to find this plant/fungus" lookup.

    Resolves the plant's stored ``inat_taxon_id`` (set during enrichment) and asks
    iNat for sightings of that taxon, scoped either to:
      - a NAMED region (``place``, e.g. "Собинский район", "окрестности Суздаля",
        "Владимирская область") — resolved to an iNat place boundary; or
      - a ``place_id`` directly; or
      - a coordinate + ``radius_km``.
    The named-region path is preferred for vernacular queries: it follows the
    real administrative/place boundary instead of a fuzzy circle. The response
    also carries ``total_count`` (how many sightings exist in scope) and
    ``seasonality`` (per-month histogram) so an agent can answer "how common" and
    "when to look". A value-add over the corpus, NOT corpus data — iNat
    attribution is returned and must be displayed. Returns an empty set with a
    note if the plant was never resolved to a taxon."""
    plant = (await db.execute(select(Plant).where(Plant.id == plant_id))).scalar_one_or_none()
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    if not plant.inat_taxon_id:
        return {"taxon_id": None, "count": 0, "observations": [],
                "note": "plant not resolved to an iNat taxon yet"}
    return await find_observations(
        plant.inat_taxon_id,
        lat=lat, lng=lng, radius_km=radius_km,
        place=place, place_id=place_id, limit=limit,
    )


@router.get("/{plant_id}/pairings")
async def plant_pairings(
    plant_id: uuid.UUID,
    category: str | None = None,
    limit: int = 15,
    db: AsyncSession = Depends(get_db),
):
    """«С чем дружит» — grounded co-occurrence pairings from the recipe corpus
    (precomputed ``plant_pairings``). Ranked by support·ln(lift): common companions
    lead, with ``specific=true`` flagging high-lift (≥6) special affinities. Each
    pairing carries evidence recipes (book + year). ``category`` slices by recipe form
    (настойка/отвар/чай/…); omit for overall. Genus rollup — a species resolves to its
    genus hub. Zero-LLM, instant (one indexed read). The flagship «сочетаемость» surface."""
    limit = max(1, min(limit, 40))
    canon = (await db.execute(text(
        "SELECT CASE WHEN par.rank='genus' THEN par.id ELSE p.id END "
        "FROM plants p LEFT JOIN plants par ON par.id=p.parent_id WHERE p.id=:i"),
        {"i": str(plant_id)})).scalar()
    if canon is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    cat = category or "__all__"
    rows = (await db.execute(text(
        "SELECT pp.plant_b, pp.support, pp.lift, pp.conf_ab, pp.sample_recipe_ids, "
        "       pb.name, pb.name_latin, pb.photo_url, pb.safety_level, pb.rank "
        "FROM plant_pairings pp JOIN plants pb ON pb.id=pp.plant_b "
        "WHERE pp.plant_a=:a AND pp.category=:c AND pp.lift>1.2 "
        "ORDER BY pp.support*ln(pp.lift) DESC LIMIT :lim"),
        {"a": str(canon), "c": cat, "lim": limit})).all()

    # evidence recipes (book + year) for the sampled recipe ids
    rids = [str(r) for row in rows for r in (row.sample_recipe_ids or [])]
    rmeta: dict[str, dict] = {}
    if rids:
        for rid, rname, btitle, byear in (await db.execute(text(
            "SELECT r.id::text, r.name, b.title, b.year FROM recipes r "
            "LEFT JOIN books b ON b.id=r.book_id WHERE r.id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": rids})).all():
            rmeta[rid] = {"id": rid, "name": rname, "book": btitle, "year": byear}

    items = []
    for row in rows:
        recs = [rmeta[str(rid)] for rid in (row.sample_recipe_ids or [])[:3] if str(rid) in rmeta]
        items.append({
            "plant": {"id": str(row.plant_b), "name": row.name, "name_latin": row.name_latin,
                      "photo_url": row.photo_url, "safety_level": row.safety_level, "rank": row.rank},
            "support": row.support, "lift": round(row.lift, 1),
            "specific": (row.lift or 0) >= 6.0, "recipes": recs,
        })
    cats = [c for (c,) in (await db.execute(text(
        "SELECT DISTINCT category FROM plant_pairings WHERE plant_a=:a AND category<>'__all__' "
        "ORDER BY category"), {"a": str(canon)})).all()]
    return {"plant_id": str(plant_id), "canon_id": str(canon),
            "category": category, "categories": cats, "items": items}


_INSIGHT_DISCLAIMER = (
    "Вот какие выводы о действии можно сделать, изучая нашу базу знаний. "
    "Это не медицинская рекомендация."
)


@router.get("/{plant_id}/compound_insights")
async def plant_compound_insights(
    plant_id: uuid.UUID,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """«Почему может работать» — состав→действие гипотезы (precomputed
    ``compound_action_assoc``). Для веществ растения берём действия, с которыми
    растения-носители этого вещества статистически связаны (гипергеометрический
    p-value, не raw lift — гасит малосэмпловые флуки). Каждая = «содержит {вещество}
    → ассоциировано с {действие}», с p/lift/support. ГИПОТЕЗА, не медсовет
    (``disclaimer``). Дедуп по действию (сильнейшее вещество-драйвер). Zero-LLM."""
    limit = max(1, min(limit, 25))
    # The plant's compound FAMILIES (merge-key normalises OCR-Greek/plural fragments and
    # drops %/number/garble), joined to the precomputed associations. Rank by lift
    # (specificity); rows are significance-gated (p<0.01) at precompute, so high-lift is
    # the «удиви меня» driver. Lowered support floor — the Fisher p-value already guards
    # small samples, so specific molecules (sparse by nature) can surface.
    comp_texts = [r[0] for r in (await db.execute(text(
        "SELECT compound FROM plant_compounds WHERE plant_id=:pid AND compound IS NOT NULL"),
        {"pid": str(plant_id)})).all()]
    keys = sorted({k for c in comp_texts if (k := compound_merge_key(c))})
    rows = []
    if keys:
        rows = (await db.execute(text(
            "SELECT compound_key, compound_display, action_canon, "
            "       support, lift, p_value, n_compound_plants "
            "FROM compound_action_assoc "
            "WHERE compound_key = ANY(:keys) AND lift >= 1.5 AND support >= 8 "
            "ORDER BY lift DESC"), {"keys": keys})).all()
    # Non-mechanistic «compounds» that carry no action signal — nutritional generics,
    # minerals, solvents and placeholders. They dominate (high coverage) and produce
    # vague/junk drivers («натрий → похудение»). Real functional classes (дубильные,
    # эфирное масло, горечи, слизь, смолы…) are kept. NOTE: deeper fix is the compound-
    # domain cleanup + phytochemistry reference data (see corpus-coverage backlog).
    _DENY = {
        "витамины", "витамин", "витамины группы в", "минеральные вещества", "минеральные соли",
        "макроэлементы", "микроэлементы", "макро- и микроэлементы", "зольные вещества", "зола",
        "клетчатка", "пищевые волокна", "вода", "белки", "жиры", "углеводы", "крахмал",
        "сахара", "сахар", "спирт", "натрий", "кальций", "калий", "магний", "фосфор",
        "биологически активные вещества", "экстрактивные вещества", "действующие вещества",
        # named vitamins + the class-descriptor «антиоксиданты» (tautology with the action)
        "никотиновая кислота", "фолиевая кислота", "аскорбиновая кислота", "пантотеновая кислота",
        "рибофлавин", "тиамин", "антиоксиданты",
    }

    def _denied(nm: str) -> bool:
        n = (nm or "").strip().lower()
        return n in _DENY or n.startswith("витамин") or n.startswith("провитамин")

    def _strength(p: float) -> str:
        if p < 1e-10:
            return "сильная"
        if p < 1e-5:
            return "заметная"
        return "умеренная"

    seen_action: set = set()
    insights = []
    for r in rows:
        nm = (r.compound_display or r.compound_key or "").strip()
        if _denied(nm) or _denied(r.compound_key):
            continue
        if r.action_canon in seen_action:     # keep the highest-lift compound per action
            continue
        seen_action.add(r.action_canon)
        insights.append({
            "compound": {"key": r.compound_key, "name": nm},
            "action": {"name": r.action_canon},
            "support": r.support, "lift": round(r.lift, 2), "p_value": r.p_value,
            "strength": _strength(r.p_value), "n_plants_with_compound": r.n_compound_plants,
        })
        if len(insights) >= limit:
            break
    return {"plant_id": str(plant_id), "disclaimer": _INSIGHT_DISCLAIMER, "insights": insights}


async def _genus_view(db: AsyncSession, genus: Plant) -> dict:
    """Hub view of a genus row: its member species + their facts AGGREGATED with
    per-species attribution (no invented genus-level facts), plus the generic
    recipe mentions that resolved to the genus. Consumed by the web card, the
    field client and MCP — all branch on ``rank == "genus"``."""
    members = (await db.execute(
        select(Plant).where(Plant.parent_id == genus.id)
        .options(selectinload(Plant.medicinal_uses).selectinload(PlantMedicinalUse.action),
                 selectinload(Plant.compounds))
        .order_by(Plant.name)
    )).scalars().all()

    book_ids: set[uuid.UUID] = set()
    for m in members:
        for u in m.medicinal_uses:
            if u.source_book_id:
                book_ids.add(u.source_book_id)
    titles: dict[str, str] = {}
    if book_ids:
        books = (await db.execute(select(Book).where(Book.id.in_(book_ids)))).scalars().all()
        titles = _book_title_map(books)

    # uses: group by action across members, keep which species report it + sources.
    use_agg: dict[str, dict] = {}
    for m in members:
        for u in m.medicinal_uses:
            act = (u.action.name if u.action else u.action_raw) or None
            if not act or is_nontarget_action(act):
                continue
            d = use_agg.setdefault(act, {"species": set(), "sources": set(), "ind": []})
            d["species"].add(m.name)
            t = titles.get(str(u.source_book_id)) if u.source_book_id else None
            if t:
                d["sources"].add(t)
            # indications is a free-text STRING (not a list) — append it whole; a bare
            # extend() would iterate the string into individual characters.
            if u.indications:
                if isinstance(u.indications, str):
                    d["ind"].append(u.indications)
                else:
                    d["ind"].extend(u.indications)
    uses = sorted(
        ({"action": a, "n_species": len(d["species"]),
          "species": sorted(d["species"])[:8],
          "indications": _distinct(d["ind"])[:6],
          "sources": sorted(d["sources"])[:5]}
         for a, d in use_agg.items()),
        key=lambda x: (-x["n_species"], x["action"]))[:40]

    # compounds: group by name across members, with species attribution.
    comp_agg: dict[str, set] = {}
    for m in members:
        for c in m.compounds:
            name = (c.compound or "").strip()
            if name:
                comp_agg.setdefault(name, set()).add(m.name)
    compounds = sorted(
        ({"compound": k, "n_species": len(v), "species": sorted(v)[:8]}
         for k, v in comp_agg.items()),
        key=lambda x: (-x["n_species"], x["compound"]))[:40]

    recipe_rows = (await db.execute(
        select(Recipe.id, Recipe.name, Recipe.category, Book.title, Book.year)
        .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
        .join(Book, Recipe.book_id == Book.id)
        .where(RecipeIngredient.plant_id == genus.id)
        .distinct().order_by(Recipe.name)
    )).all()
    recipes = [{"id": str(r[0]), "name": r[1], "category": r[2], "book": r[3], "year": r[4]}
               for r in recipe_rows]

    # Forager-safety across the genus: worst-case level + which members are dangerous
    # (a genus is browsed when the recipe said «вишня» — show the cautious envelope).
    levels = [m.safety_level for m in members if m.safety_level is not None]
    genus_safety = None
    if levels:
        worst = max(levels)
        genus_safety = {
            "level": worst,
            "label": SAFETY_LABELS.get(worst, "нет данных"),
            "note": "уровень опасности различается по видам — сверяйтесь с конкретным видом",
            "dangerous_members": [
                {"id": str(m.id), "name": m.name, "level": m.safety_level,
                 "deadly_twin": m.deadly_twin}
                for m in members if (m.safety_level or 0) >= 3
            ][:12],
        }

    return {
        "id": str(genus.id),
        "name": genus.name,
        "name_latin": genus.name_latin,
        "rank": "genus",
        "kingdom": genus.kingdom,
        "safety": genus_safety,
        "member_count": len(members),
        "members": [{"id": str(m.id), "name": m.name, "name_latin": m.name_latin}
                    for m in members],
        "uses": uses,
        "compounds": compounds,
        "recipes": recipes,
        "note": (f"Род «{genus.name}» — обобщает виды рода; факты приведены с "
                 f"атрибуцией по видам. Рецепты с недоопределённым названием "
                 f"привязаны сюда — конкретный вид смотрите в карточке вида."),
    }


@router.get("/{plant_id}")
async def get_plant(
    plant_id: uuid.UUID,
    view: str | None = None,
    fresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Full plant monograph: identity + all source-layered facts.

    Default response (no ``view``) is the raw, source-layered dump consumed by
    the MCP ``get_plant`` tool and the web monograph — kept byte-for-byte stable.
    ``?view=field`` returns a compact, deduped + ranked aggregation for the
    low-bandwidth field client (see docs/HANDOFF-monograph-aggregation.md).
    """
    load_options = [
        selectinload(Plant.medicinal_uses).selectinload(PlantMedicinalUse.action),
        selectinload(Plant.compounds),
        selectinload(Plant.harvests),
        selectinload(Plant.habitats),
        selectinload(Plant.biotopes),
        selectinload(Plant.toxicities),
        selectinload(Plant.culinary_uses),
        selectinload(Plant.mentions),
    ]
    if view == "field":
        # field view surfaces each compound's vocabulary definition; eager-load
        # the Compound ref so we don't N+1. Default view never touches it.
        load_options.append(
            selectinload(Plant.compounds).selectinload(PlantCompound.compound_ref)
        )
    stmt = select(Plant).where(Plant.id == plant_id).options(*load_options)
    plant = (await db.execute(stmt)).scalar_one_or_none()
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")

    # Genus tier (RFC-reference-granularity): a genus row is a HUB with no own
    # facts — it aggregates its member species (with per-species attribution) and
    # owns the generic «вишня»-style recipe mentions. Same shape for default+field.
    if (plant.rank or "species") == "genus":
        return await _genus_view(db, plant)

    # Layer 2: if a vetted reader-monograph exists, serve it (humans get the
    # precomputed prose; recipes/sources are baked in at generation time). Falls
    # through to the deterministic aggregation when none is published yet.
    # `?fresh=1` bypasses this so the generator/regen always reads raw aggregation.
    if view == "field" and not fresh:
        stored = (await db.execute(
            select(PlantReaderMonograph).where(
                PlantReaderMonograph.plant_id == plant_id,
                PlantReaderMonograph.reviewed.is_(True)))).scalar_one_or_none()
        if stored is not None:
            return stored.monograph

    # Collect every source_book_id referenced across the child rows, then resolve
    # titles in one query for human-readable source attribution.
    book_ids: set[uuid.UUID] = set()
    for u in plant.medicinal_uses:
        if u.source_book_id:
            book_ids.add(u.source_book_id)
    for c in plant.compounds:
        if c.source_book_id:
            book_ids.add(c.source_book_id)
    for h in plant.harvests:
        if h.source_book_id:
            book_ids.add(h.source_book_id)
    for h in plant.habitats:
        if h.source_book_id:
            book_ids.add(h.source_book_id)
    for t in plant.toxicities:
        if t.source_book_id:
            book_ids.add(t.source_book_id)
    for cu in plant.culinary_uses:
        if cu.source_book_id:
            book_ids.add(cu.source_book_id)
    for m in plant.mentions:
        book_ids.add(m.book_id)

    titles: dict[str, str] = {}
    if book_ids:
        books = (await db.execute(select(Book).where(Book.id.in_(book_ids)))).scalars().all()
        titles = _book_title_map(books)

    def src(book_id) -> str | None:
        return titles.get(str(book_id)) if book_id else None

    # Cross-domain link: recipes whose ingredients resolved to this plant.
    recipe_rows = (await db.execute(
        select(Recipe.id, Recipe.name, Recipe.category, Book.title, Book.year)
        .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
        .join(Book, Recipe.book_id == Book.id)
        .where(RecipeIngredient.plant_id == plant_id)
        .distinct()
        .order_by(Recipe.name)
    )).all()
    recipes = [
        {
            "id": str(rid),
            "name": rname,
            "category": rcat,
            "book": btitle,
            "year": byear,
        }
        for (rid, rname, rcat, btitle, byear) in recipe_rows
    ]

    # Genus backlink: a member species points up to its hub so the client can
    # offer «другие виды рода» / drill back. Cheap single lookup, null for the rest.
    parent = None
    if plant.parent_id:
        pr = (await db.execute(select(Plant.id, Plant.name, Plant.name_latin)
                               .where(Plant.id == plant.parent_id))).first()
        if pr:
            parent = {"id": str(pr[0]), "name": pr[1], "name_latin": pr[2]}

    # Opt-in compact aggregation for the field client. Returned BEFORE the
    # essential-oil query so the default (raw) path is wholly untouched.
    if view == "field":
        fv = _field_view(plant, src, recipes)
        fv["parent"] = parent
        return fv

    # Cross-pillar link: essential oils distilled/pressed from this plant. The
    # aroma pillar bridges each oil to its source plant via EssentialOil.plant_id;
    # surface that reverse edge so the plant card shows "oils made from me".
    oil_rows = (await db.execute(
        select(EssentialOil.id, EssentialOil.name, EssentialOil.name_latin,
               EssentialOil.part, EssentialOil.extraction,
               func.count(EssentialOilUse.id))
        .join(EssentialOilUse, EssentialOilUse.oil_id == EssentialOil.id, isouter=True)
        .where(EssentialOil.plant_id == plant_id)
        .group_by(EssentialOil.id)
        .order_by(EssentialOil.name)
    )).all()
    essential_oils = [
        {
            "id": str(oid),
            "name": oname,
            "name_latin": olatin,
            "part": opart,
            "extraction": oextr,
            "uses_count": ucount,
        }
        for (oid, oname, olatin, opart, oextr, ucount) in oil_rows
    ]

    return {
        "id": str(plant.id),
        "name": plant.name,
        "name_latin": plant.name_latin,
        "name_modern": plant.name_modern,
        "names_historical": plant.names_historical,
        "rank": plant.rank,
        "parent": parent,
        "family": plant.family,
        "family_latin": plant.family_latin,
        "description": plant.description,
        "parts_used": plant.parts_used,
        "rank": plant.rank,
        "is_toxic": plant.is_toxic,
        "safety": _safety_block(plant),
        "kingdom": plant.kingdom,
        "photo_url": plant.photo_url,
        "photo_attribution": plant.photo_attribution,
        "photo_license": plant.photo_license,
        "photo_source": plant.photo_source,
        "inat_taxon_id": plant.inat_taxon_id,
        "medicinal_uses": [
            {
                "id": str(u.id),
                "part": u.part,
                "action": u.action.name if u.action else u.action_raw,
                "action_system": u.action.system if u.action else None,
                "indications": u.indications,
                "indication_ids": [str(i) for i in (u.indication_ids or [])],
                "preparation": u.preparation,
                "dosage": u.dosage,
                "contraindications": u.contraindications,
                "original_text": u.original_text,
                "confidence": u.confidence,
                "source": src(u.source_book_id),
            }
            for u in plant.medicinal_uses
        ],
        "compounds": [
            {
                "id": str(c.id),
                "compound": c.compound,
                "compound_group": c.compound_group,
                "compound_id": str(c.compound_id) if c.compound_id else None,
                "part": c.part,
                "notes": c.notes,
                "source": src(c.source_book_id),
            }
            for c in plant.compounds
        ],
        "harvests": [
            {
                "id": str(h.id),
                "part": h.part,
                "season": h.season,
                "method": h.method,
                "original_text": h.original_text,
                "source": src(h.source_book_id),
            }
            for h in plant.harvests
        ],
        "habitats": [
            {
                "id": str(h.id),
                "region": h.region,
                "biotope": h.biotope,
                "status": h.status,
                "original_text": h.original_text,
                "source": src(h.source_book_id),
            }
            for h in plant.habitats
        ],
        "toxicities": [
            {
                "id": str(t.id),
                "toxic_parts": t.toxic_parts,
                "symptoms": t.symptoms,
                "antidote": t.antidote,
                "severity": t.severity,
                "original_text": t.original_text,
                "source": src(t.source_book_id),
            }
            for t in plant.toxicities
        ],
        "culinary_uses": [
            {
                "id": str(cu.id),
                "part": cu.part,
                "edibility": cu.edibility,
                "preparation": cu.preparation,
                "use": cu.use,
                "season": cu.season,
                "caution": cu.caution,
                "original_text": cu.original_text,
                "confidence": cu.confidence,
                "source": src(cu.source_book_id),
            }
            for cu in plant.culinary_uses
        ],
        "mentions": [
            {
                "id": str(m.id),
                "book": titles.get(str(m.book_id)),
                "original_name": m.original_name,
                "page_number": m.page_number,
            }
            for m in plant.mentions
        ],
        "recipes": recipes,
        "essential_oils": essential_oils,
    }
