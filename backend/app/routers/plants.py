import re
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
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
    PlantToxicity,
    PlantCulinaryUse,
    PlantBookMention,
    EssentialOil,
    EssentialOilUse,
)
from app.services.plant_matching import relink_recipe_ingredients, merge_plants_by_latin_key
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

    # Total matching rows (before pagination) → header for the herbarium UI.
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)

    rows = (await db.execute(stmt)).all()
    return [_plant_summary(p, n) for p, n in rows]


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

    action_count = func.count(func.distinct(PlantMedicinalUse.plant_id))
    actions = (await db.execute(
        select(MedicinalAction.name, action_count)
        .join(PlantMedicinalUse, PlantMedicinalUse.action_id == MedicinalAction.id)
        .group_by(MedicinalAction.name)
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
        actions = _split_actions(u.action.name if u.action else None, u.action_raw)
        # clean ICD/MKB codes out of the indications once per row (shared by every
        # action split out of this row)
        row_indications = [
            cl for ph in _split_phrases(u.indications)
            if (cl := _clean_indication(ph))
        ]
        row_indication_ids = [str(i) for i in (u.indication_ids or [])]
        for action in actions:
            key = action.lower()
            g = use_groups.get(key)
            if g is None:
                g = {
                    "action": action,
                    "_parts": [],
                    "_indications": [],
                    "_indication_ids": [],
                    "source_count": 0,
                    "_max_conf": 0.0,
                }
                use_groups[key] = g
            g["source_count"] += 1
            if u.confidence is not None and u.confidence > g["_max_conf"]:
                g["_max_conf"] = u.confidence
            if u.part:
                g["_parts"].append(u.part)
            g["_indications"].extend(row_indications)
            g["_indication_ids"].extend(row_indication_ids)

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
            seen_ind.add(lk)
            ranked_ind.append(phrase)
            if len(ranked_ind) >= 6:
                break
        uses.append({
            "action": g["action"],
            "parts": _distinct(g["_parts"]),
            "indications": ranked_ind,
            "indication_ids": _distinct(g["_indication_ids"]),
            "source_count": g["source_count"],
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
        if ex is None:
            g["_examples"][name.lower()] = {"name": name, "compound_id": cid}
        elif ex["compound_id"] is None and cid:
            ex["compound_id"] = cid  # prefer the tappable variant
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
        "uses": uses,
        "cautions": cautions,
        "compound_groups": compound_groups,
        "harvest": harvest,
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


@router.get("/{plant_id}")
async def get_plant(
    plant_id: uuid.UUID,
    view: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Full plant monograph: identity + all source-layered facts.

    Default response (no ``view``) is the raw, source-layered dump consumed by
    the MCP ``get_plant`` tool and the web monograph — kept byte-for-byte stable.
    ``?view=field`` returns a compact, deduped + ranked aggregation for the
    low-bandwidth field client (see docs/HANDOFF-monograph-aggregation.md).
    """
    stmt = (
        select(Plant)
        .where(Plant.id == plant_id)
        .options(
            selectinload(Plant.medicinal_uses).selectinload(PlantMedicinalUse.action),
            selectinload(Plant.compounds),
            selectinload(Plant.harvests),
            selectinload(Plant.habitats),
            selectinload(Plant.toxicities),
            selectinload(Plant.culinary_uses),
            selectinload(Plant.mentions),
        )
    )
    plant = (await db.execute(stmt)).scalar_one_or_none()
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")

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

    # Opt-in compact aggregation for the field client. Returned BEFORE the
    # essential-oil query so the default (raw) path is wholly untouched.
    if view == "field":
        return _field_view(plant, src, recipes)

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
        "family": plant.family,
        "family_latin": plant.family_latin,
        "description": plant.description,
        "parts_used": plant.parts_used,
        "is_toxic": plant.is_toxic,
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
