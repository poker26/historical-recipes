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
        "live in the same herbarium (filter by `kingdom`). Use the search_/list_/"
        "find_ tools to discover entities and the controlled vocabulary, then "
        "get_plant / get_recipe for the full source-grounded monograph or recipe "
        "(each fact carries its source book + year). find_observations_nearby adds "
        "live iNaturalist sightings for a plant near a coordinate."
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
        indication: what it is used for (free text over indications).
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
async def list_vocabulary(kind: str) -> list | dict:
    """List the controlled-vocabulary values available as filters, each with a
    plant count. Call this BEFORE forming a structured search_plants query so you
    use real filter values instead of guessing.

    Args:
        kind: one of "actions" (medicinal actions), "compound_groups" (constituent
            groups), "edibility", "kingdom", or "all".

    Returns a list of {value, count} (or, for "all", the full facets object)."""
    _record_usage("list_vocabulary", "discovery")
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
        return {"error": f"unknown kind {kind!r}; use actions|compound_groups|edibility|kingdom|all"}
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
