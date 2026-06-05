"""Remote MCP server: read-only retrieval over the historical-recipes corpus.

Runs as its OWN container (``python -m app.mcp.server``) that reuses the backend
image — same code + deps, like the Temporal worker. It is the consumer-facing
surface: an external agent connects over Streamable HTTP (behind nginx at
``/mcp``), brings its own LLM, and calls these tools. We sell metered access to
the curated DATA, not LLM traffic.

Design:
- **No SQL here.** Every tool is a thin wrapper over the existing FastAPI routers,
  reached over the internal docker network (``settings.internal_api_url``). All
  query logic stays in one place (the routers); the MCP layer only translates
  tool calls ⇆ REST calls and shapes the result.
- **Tier shape is built in but the gate is dormant** (monetization not wired yet,
  2026-06-04). ``_record_usage`` is the metering seam: today it only logs; later
  it records to ``api_usage`` keyed by API key and the full ``get_*`` tier gets a
  hard quota + rate-limit. The anti-exfiltration boundary is LIST-vs-DOCUMENT: a
  ``search_*``/``list_*`` tool returns hits; the full grounded ``original_text``
  of a specific entity comes only from a metered ``get_*``.
"""

import logging

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("mcp.server")

API = settings.internal_api_url.rstrip("/")

mcp = FastMCP(
    "historical-recipes",
    instructions=(
        "Structured knowledge base of historical Russian herbalism, mycology, "
        "phytochemistry and recipes, built from digitised books. Plants AND fungi "
        "live in the same herbarium (filter by `kingdom`). Discovery-first: use the "
        "list_vocabulary / find_compound / find_indication tools to resolve real "
        "controlled-vocabulary values (with the archaic→modern bridge, e.g. водянка→"
        "отёки), THEN search_plants with those values, THEN get_plant / get_recipe for "
        "the full source-grounded monograph (every fact carries its source book + "
        "year). compound_associations mines compound→indication/action co-occurrence "
        "as a hypothesis lead (association via the plant, NOT a causal claim — read its "
        "p_value, never say 'X cures Y'). find_observations_nearby adds live "
        "iNaturalist sightings near a coordinate. See AGENTS.md for the full tool guide."
    ),
    host="0.0.0.0",
    port=8200,
    stateless_http=True,
)


async def _request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict | list:
    """One HTTP round-trip to the internal REST API. Strips None params so optional
    tool args don't become literal `None` query values. Returns parsed JSON, or an
    ``{"error": ...}`` dict so a tool degrades gracefully instead of raising."""
    if params:
        params = {k: v for k, v in params.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.request(method, f"{API}{path}", params=params, json=json)
    except httpx.HTTPError as e:
        logger.warning(f"internal API {method} {path} failed: {type(e).__name__}: {e}")
        return {"error": f"backend request failed: {e}"}
    if resp.status_code == 404:
        return {"error": "not found"}
    if resp.status_code >= 400:
        return {"error": f"backend HTTP {resp.status_code}", "detail": resp.text[:500]}
    try:
        return resp.json()
    except ValueError:
        return {"error": "backend returned non-JSON body"}


def _record_usage(tool: str, tier: str) -> None:
    """Metering seam (dormant). Logs the call now; later records to api_usage keyed
    by API key and enforces quota/rate-limit on the `full` tier."""
    logger.info(f"mcp_usage tool={tool} tier={tier}")


# ─────────────────────────── Discovery tier (funnel) ───────────────────────────


@mcp.tool()
async def search_plants(
    q: str | None = None,
    compound: str | None = None,
    action: str | None = None,
    indication: str | None = None,
    family: str | None = None,
    toxic: bool | None = None,
    edibility: str | None = None,
    kingdom: str | None = None,
) -> list | dict:
    """Search the herbarium of PLANTS AND FUNGI by free text and/or structured
    facets (all combine with AND). This is the way to answer cross-vocabulary
    questions a plain LLM cannot ground, e.g. "non-toxic Lamiaceae with sedative
    action" or "edible fungi".

    Args:
        q: free text over name / Latin name / historical names.
        compound: constituent substance or group (free-text match), e.g.
            "дубильные вещества", "алкалоиды".
        action: medicinal action (normalized vocabulary + raw), e.g. "седативное".
            Use list_vocabulary("actions") to see valid values.
        indication: what it is used for — a symptom/disease. Resolved through the
            indication vocabulary incl. the archaic→modern bridge ("водянка" finds
            "отёки"), with a free-text fallback. Use find_indication to see concepts.
        family: botanical/fungal family (Russian or Latin), e.g. "Розоцветные".
        toxic: True → only toxic taxa, False → only non-toxic.
        edibility: "съедобно" | "условно-съедобно" | "несъедобно" | "ядовито".
        kingdom: "растение" | "гриб". Omit for both.

    Returns a list of cards (id, name, name_latin, family, is_toxic, kingdom,
    parts_used, uses_count, photo_url, photo_attribution). Fetch a full monograph
    with get_plant(id)."""
    _record_usage("search_plants", "discovery")
    return await _request("GET", "/api/plants/", params={
        "q": q, "compound": compound, "action": action, "indication": indication,
        "family": family, "is_toxic": toxic, "edibility": edibility, "kingdom": kingdom,
    })


@mcp.tool()
async def search_recipes(
    q: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    book_id: str | None = None,
) -> list | dict:
    """Search recipes by free text and/or filters.

    Args:
        q: free text over recipe name and original text.
        category: recipe category (e.g. водка/ликёр/настойка/бальзам/масло, or a
            culinary/medicinal class).
        domain: source-book domain — "recipes" for culinary recipes, "herbalism"
            for medicinal preparations (отвары/настои/сборы) extracted from
            herbals. This is the only way to separate culinary from medicinal,
            since a recipe inherits its domain from its source book.
        book_id: restrict to one source book (UUID).

    Returns a list (id, name, category, book title/author/year, original_text).
    Fetch full detail with get_recipe(id)."""
    _record_usage("search_recipes", "discovery")
    return await _request("GET", "/api/recipes/", params={
        "q": q, "category": category, "domain": domain, "book_id": book_id,
    })


@mcp.tool()
async def semantic_search(query: str, collection: str | None = None, limit: int = 10) -> dict:
    """Hybrid (dense+sparse) semantic search across the corpus. Use this for
    natural-language questions whose answer lives in free prose rather than a
    structured field (e.g. "чем лечили лихорадку в банях").

    Args:
        query: natural-language query (Russian works best).
        collection: optionally restrict — "recipes_v2" (recipes), "plants_v2"
            (plant/fungi monographs) or "sections_v1" (book passages). Omit to
            search all three.
        limit: max hits (default 10).

    Returns ranked hits with id, collection, score and a payload. Use the ids with
    get_plant / get_recipe for the full grounded text."""
    _record_usage("semantic_search", "discovery")
    return await _request("POST", "/api/search/", json={
        "query": query, "collection": collection, "limit": limit, "mode": "hybrid",
    })


@mcp.tool()
async def list_vocabulary(kind: str, limit: int = 50) -> list | dict:
    """List the controlled-vocabulary values available as filters, each with a
    count. Call this BEFORE forming a structured search_plants query so you use
    real filter values instead of guessing.

    Args:
        kind: one of "actions" (medicinal actions), "indications" (показания —
            symptoms/diseases a remedy is used FOR), "compound_groups" (constituent
            groups), "edibility", "kingdom", or "all".
        limit: max values for "indications" (the indication vocabulary is large, so
            only the best-covered concepts are returned). Ignored for other kinds.

    Returns a list of {value, count} (for "indications" also name_modern + id so you
    can pass it to get_indication), or, for "all", the full facets object."""
    _record_usage("list_vocabulary", "discovery")
    # The indication axis isn't a plant facet — it lives in the medical vocabulary
    # and is large (thousands of concepts), so surface only the best-covered ones,
    # ranked by how many use-facts each normalizes (its real corpus weight).
    if kind == "indications":
        rows = await _request("GET", "/api/medical/indications")
        if isinstance(rows, dict):  # error
            return rows
        rows = [r for r in rows if (r.get("linked_facts") or 0) > 0]
        rows.sort(key=lambda r: r.get("linked_facts") or 0, reverse=True)
        return [
            {
                "value": r.get("name"),
                "name_modern": r.get("name_modern"),
                "count": r.get("linked_facts") or 0,
                "id": r.get("id"),
            }
            for r in rows[:limit]
        ]
    facets = await _request("GET", "/api/plants/facets")
    if isinstance(facets, dict) and "error" in facets:
        return facets
    if kind == "all":
        return facets
    key = {
        "actions": "actions",
        "compound_groups": "compound_groups",
        "edibility": "edibility",
        "kingdom": "kingdom",
    }.get(kind)
    if key is None:
        return {"error": f"unknown kind {kind!r}; use actions|indications|compound_groups|edibility|kingdom|all"}
    return facets.get(key, [])


@mcp.tool()
async def find_compound(q: str | None = None, limit: int = 50) -> list | dict:
    """Browse the controlled COMPOUND vocabulary (phytochemistry: glycosides,
    alkaloids, flavonoids, …) with hierarchy, synonyms and how many plant-facts
    each term normalizes. Use a returned id with get_compound to list the plants
    that contain it.

    Args:
        q: optional substring filter over name / Latin name / class / synonyms.
        limit: max terms to return (default 50).

    Returns vocabulary terms (id, name, name_latin, compound_class, synonyms,
    parent_id, linked_facts)."""
    _record_usage("find_compound", "discovery")
    rows = await _request("GET", "/api/compounds")
    if isinstance(rows, dict):  # error
        return rows
    if q:
        ql = q.strip().lower()
        def hit(c: dict) -> bool:
            hay = " ".join(filter(None, [
                c.get("name"), c.get("name_latin"), c.get("compound_class"),
                " ".join(c.get("synonyms") or []),
            ])).lower()
            return ql in hay
        rows = [c for c in rows if hit(c)]
    return rows[:limit]


@mcp.tool()
async def get_compound(compound_id: str) -> dict:
    """A single compound vocabulary term plus the PLANTS whose composition
    normalizes to it (reverse lookup) and its place in the hierarchy. This answers
    "which plants contain X" authoritatively, e.g. cardiac glycosides → the plants
    that contain them.

    Args:
        compound_id: UUID from find_compound.

    Returns the term (name, class, synonyms, definition, parent/children) and a
    `plants` list (id, name, name_latin, parts, raw_names). Read each plant in full
    with get_plant(id)."""
    _record_usage("get_compound", "discovery")
    return await _request("GET", f"/api/compounds/{compound_id}")


@mcp.tool()
async def compound_associations(compound_id: str, axis: str = "indication",
                                limit: int = 20, min_support: int = 2) -> dict:
    """Hypothesis generator: given a COMPOUND, which conditions (indications) or
    medicinal actions do the plants that contain it tend to be used for? Answers the
    "if a plant has substance X, it probably helps with Y" question — but as a
    statistical ASSOCIATION through the bridging plant, NOT a causal claim.

    The corpus never says "X treats Y"; it records, separately, that a plant CONTAINS X
    and that the same plant is USED FOR Y. This tool mines that co-occurrence over the
    plants having both chemistry and medicinal data, ranking targets by a one-sided
    hypergeometric p_value (small = unlikely by chance). Read p_value FIRST: with few
    plants per compound, `lift` inflates on 2-plant coincidences, so a high lift with
    support=2 and p_value≈0.1 is weak. Each row lists the supporting plants — verify the
    claim by reading those monographs with get_plant.

    HOW TO PHRASE RESULTS to a user: "plants containing X are often used for Y
    (co-occurrence across N plants)", never "X cures Y". Compounds come largely from
    modern phytochemistry sources, indications partly from historical herbals — this is
    a research lead, not medical advice.

    Args:
        compound_id: UUID from find_compound. Querying a compound CLASS (e.g.
            флавоноиды) automatically includes its hierarchy descendants (рутин,
            кверцетин…), which gives more statistical support.
        axis: "indication" (specific conditions; sparser, noisier) or "action"
            (broad medicinal actions like ангиопротекторное; more support per
            category, closer to mechanism — prefer this for a robust first look).
        min_support: minimum plants backing an association (default 2). Raise to 3–4 to
            hide thin coincidences.
        limit: max ranked targets (default 20).

    Returns {source, axis, n_base, n_source, note, results:[{name, support, lift,
    p_value, target_plants, plants:[…]}]}, most-significant first."""
    _record_usage("compound_associations", "discovery")
    return await _request(
        "GET", f"/api/compounds/{compound_id}/associations",
        params={"axis": axis, "limit": limit, "min_support": min_support},
    )


@mcp.tool()
async def find_indication(q: str | None = None, system: str | None = None,
                           min_facts: int = 0, limit: int = 50) -> list | dict:
    """Browse the controlled INDICATION vocabulary (показания: what a remedy is used
    FOR — symptoms and diseases) with hierarchy, modern/archaic names and how many
    use-facts each concept normalizes. Its headline is the archaic→modern BRIDGE:
    "водянка" lives under the modern concept "отёки", so a 19th-c. term and a modern
    one resolve to the same row. Use a returned id with get_indication to list the
    plants that treat it.

    Results are sorted by coverage (linked_facts, descending), so the canonical,
    well-attested concept floats above thin near-duplicate scraps that a broad query
    like "водянка" can otherwise surface.

    Args:
        q: optional substring filter over name / modern name / synonyms / archaic
            names (matches an archaic query like "грудная жаба" too).
        system: optional body-system filter (substring, case-insensitive) over the
            concept's `system` tag — e.g. "дыхание", "ЖКТ", "ССС", "ЦНС", "кожа",
            "мочеполовая". Browse one body system without a keyword: pass system=
            with no q to list every well-covered condition in that system.
        min_facts: drop concepts normalizing fewer than this many use-facts
            (default 0 = keep all). Set e.g. 5 to see only well-covered conditions
            and hide long-tail singletons.
        limit: max concepts to return (default 50).

    Returns vocabulary concepts (id, name, name_modern, synonyms, archaic, system,
    parent_id, linked_facts), most-covered first."""
    _record_usage("find_indication", "discovery")
    rows = await _request("GET", "/api/medical/indications")
    if isinstance(rows, dict):  # error
        return rows
    if q:
        ql = q.strip().lower()
        def hit(c: dict) -> bool:
            hay = " ".join(filter(None, [
                c.get("name"), c.get("name_modern"),
                " ".join(c.get("synonyms") or []), " ".join(c.get("archaic") or []),
            ])).lower()
            return ql in hay
        rows = [c for c in rows if hit(c)]
    if system:
        sl = system.strip().lower()
        rows = [c for c in rows if sl in (c.get("system") or "").lower()]
    if min_facts > 0:
        rows = [c for c in rows if (c.get("linked_facts") or 0) >= min_facts]
    rows.sort(key=lambda c: c.get("linked_facts") or 0, reverse=True)
    return rows[:limit]


@mcp.tool()
async def get_indication(indication_id: str) -> dict:
    """A single indication concept plus the PLANTS whose medicinal uses normalize to
    it (concept + its hierarchy descendants). Answers "какие растения при X"
    authoritatively, including via archaic names.

    Args:
        indication_id: UUID from find_indication.

    Returns the concept (name, name_modern, synonyms, archaic, system, definition,
    parent/children) and a `plants` list (id, name, name_latin, parts,
    raw_indications). Read each plant in full with get_plant(id)."""
    _record_usage("get_indication", "discovery")
    return await _request("GET", f"/api/medical/indications/{indication_id}")


@mcp.tool()
async def plants_for_condition(
    condition: str,
    kingdom: str | None = None,
    toxic: bool | None = None,
    limit: int = 50,
) -> dict:
    """Find plants/fungi for a medical CONDITION expressed as a user would — a
    symptom or disease ("кашель", "бессонница") OR an archaic disease name
    ("водянка", "грудная жаба") OR a medicinal action ("отхаркивающее"). Resolves
    the term across BOTH medical axes — the indication axis (показания, with the
    archaic→modern bridge) and the action axis (действие) — and unions the plants,
    because a historical corpus records "what it treats" on either axis. This is the
    medical entry point; for finer control use search_plants(indication=…, action=…).

    Args:
        condition: a symptom, disease, archaic disease name, or medicinal action.
        kingdom: "растение" | "гриб". Omit for both.
        toxic: True → only toxic, False → only non-toxic.
        limit: max plants to return (default 50).

    Returns {condition, count, plants:[…]} where each plant is a card (id, name,
    name_latin, family, is_toxic, kingdom, uses_count, photo…). Read a full grounded
    monograph with get_plant(id); use find_indication to inspect the concept."""
    _record_usage("plants_for_condition", "discovery")
    base = {"kingdom": kingdom, "is_toxic": toxic}
    by_ind = await _request("GET", "/api/plants/", params={**base, "indication": condition})
    by_act = await _request("GET", "/api/plants/", params={**base, "action": condition})
    merged: dict[str, dict] = {}
    for rows in (by_ind, by_act):
        if isinstance(rows, list):
            for p in rows:
                pid = p.get("id")
                if pid and pid not in merged:
                    merged[pid] = p
    if not merged and isinstance(by_ind, dict) and "error" in by_ind:
        return by_ind
    plants = list(merged.values())[:limit]
    return {"condition": condition, "count": len(plants), "plants": plants}


# ────────────────────────────── Full tier (product) ─────────────────────────────


@mcp.tool()
async def get_plant(plant_id: str) -> dict:
    """Full monograph for one plant or fungus: identity (names, Latin, family,
    kingdom, toxicity, iNat photo+attribution) and ALL source-layered facts —
    medicinal uses, compounds, harvests, habitats, toxicities, culinary uses — each
    with its verbatim `original_text` and the source book + year, plus cross-linked
    recipes that use it. This grounded source text is the product.

    Args:
        plant_id: UUID from search_plants / semantic_search / get_compound."""
    _record_usage("get_plant", "full")
    return await _request("GET", f"/api/plants/{plant_id}")


@mcp.tool()
async def get_recipe(recipe_id: str) -> dict:
    """Full recipe: verbatim original_text + normalized text + ingredient list
    (each linked to its plant in the herbarium where resolved), with source book /
    author / year.

    Args:
        recipe_id: UUID from search_recipes / semantic_search."""
    _record_usage("get_recipe", "full")
    return await _request("GET", f"/api/recipes/{recipe_id}")


# ─────────────────────────── Identification tier (photo → species) ───────────────


@mcp.tool()
async def identify_plant(
    image_urls: list[str],
    organs: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """Identify a plant/fungus from one or more PHOTO URLs and link each candidate
    species to our herbarium. This is the entry point for "what is this plant, and
    what does our historical corpus say about it": identify → get_plant.

    Args:
        image_urls: 1–5 publicly fetchable image URLs of the same plant (different
            organs/angles improve accuracy).
        organs: optional, one per image — "leaf"|"flower"|"fruit"|"bark"|"auto"
            (a single value applies to all; omit to auto-detect).
        limit: max candidate species (default 5).

    Returns ``{engine, candidates:[…], matched_count, remaining_requests}``. Each
    candidate has ``latin``, ``score`` (0–1 confidence), ``common_names``,
    ``gbif_id``, and ``plant``: a herbarium card (id, name, name_latin, …) when the
    species IS in our corpus, else null. For a matched candidate, call
    get_plant(plant.id) for the full source-grounded monograph; for an unmatched
    one, the species simply isn't in our books yet. Identification is an external
    engine (Pl@ntNet); confidence is the engine's, not ours."""
    _record_usage("identify_plant", "identify")
    return await _request("POST", "/api/identify/by-url", json={
        "image_urls": image_urls, "organs": organs, "limit": limit,
    })


# ─────────────────────── Live-enrichment tier (iNaturalist) ──────────────────────


@mcp.tool()
async def find_observations_nearby(
    plant_id: str,
    region: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 50.0,
    limit: int = 20,
) -> dict:
    """Live iNaturalist sightings of a plant/fungus — "where can I find this, and
    how common / when is it seen". This is live external data (not the corpus):
    iNat attribution is returned and must be displayed.

    Scope the search EITHER by a named region OR by a coordinate:
      - region: a place name at any granularity — district, town vicinity, or
        oblast (e.g. "Собинский район", "окрестности Суздаля", "Владимирская
        область"). PREFER this for vernacular "where in X" questions: it follows
        the real place boundary rather than a fuzzy circle. The name is resolved
        to an iNat place server-side (largest matching boundary wins).
      - lat + lng (+ radius_km): a coordinate and circular radius. Use when you
        have exact coordinates rather than a place name.
    If both are given, region takes precedence.

    Args:
        plant_id: UUID of the plant (its iNat taxon is resolved server-side).
        region: place name to scope to (see above).
        lat, lng: centre coordinate (decimal degrees), alternative to region.
        radius_km: search radius in km for the coordinate path (default 50).
        limit: max observations (default 20, capped at 50).

    Returns the resolved scope plus ``total_count`` (how many sightings exist in
    that region/radius — a commonness signal), ``seasonality`` (per-month
    histogram of when it's observed), and a sample of observations (date, place,
    location, photo+attribution, observer, iNat uri), newest first. Empty with a
    note if the plant was never resolved to an iNat taxon."""
    _record_usage("find_observations_nearby", "live")
    params: dict = {"radius_km": radius_km, "limit": limit}
    if region:
        params["place"] = region
    if lat is not None:
        params["lat"] = lat
    if lng is not None:
        params["lng"] = lng
    return await _request("GET", f"/api/plants/{plant_id}/observations", params=params)


if __name__ == "__main__":
    # Streamable HTTP transport: FastMCP serves the app via uvicorn on host:port,
    # mounting the endpoint at /mcp (the default streamable_http_path). nginx
    # proxies the public /mcp here with buffering off for SSE streaming.
    logger.info(f"MCP server starting on :8200/mcp — backend at {API}")
    mcp.run(transport="streamable-http")
