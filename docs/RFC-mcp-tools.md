# RFC: MCP toolset over the knowledge base

Status: **Design agreed, not built** (2026-06-04) · Author: design notes · Created: 2026-06-04

> The KB's value is the curated DATA, not an agent ("anyone can build an agent in
> 5 minutes"). So we expose metered, read-only RETRIEVAL over the corpus via a
> remote MCP server; the consumer brings their own LLM. This RFC fixes the
> concrete toolset, grounded in the query surface that already exists in the API
> routers (`plants.py`, `recipes.py`, `search.py`, `compounds.py`) — the MCP
> tools wrap that surface, they do not invent new retrieval.

## Strategic frame (carried from the monetization design)

- **Sell access to data, not LLM traffic.** No hosting third-party LLM calls.
- **Billing lever = data depth**, which is also the anti-exfiltration control:
  - **Discovery tier** (cheap/free funnel): metadata + results from `search_*` / `list_*`.
  - **Full tier** (paid, hard quota + rate-limit): full `original_text` from `get_*`.
- **Monetization is NOT being built yet** (2026-06-04). Until then there is no
  paid gate and no "reselling" concern for live-passthrough enrichment (iNat).
  The tier split is still the right *shape* — we build it in, but it's dormant.
- **Snippet trimming: NOT done.** Decided 2026-06-04: discovery `search_*` tools
  return the full text. The funnel holds on the LIST-vs-DOCUMENT boundary (a
  search returns a list of hits; reading a specific grounded monograph/recipe is
  a separate `get_*` call), not on truncating text.

## Query surface available today (the raw material)

Three retrieval pillars plus controlled vocabularies, all already implemented:

| Pillar | Structured filters (what a bare LLM cannot do) | Full text |
|---|---|---|
| **Plants & fungi** (`plants_v2`, `GET /api/plants`) | q, compound, action (normalized vocab + raw), indication, family, is_toxic, edibility/edible, **kingdom (растение\|гриб)** | monograph: medicinal/culinary uses, compounds, harvests, habitats, toxicities — each fact carries `original_text` + source book + year |
| **Recipes** (`recipes_v2`, `GET /api/recipes`) | category, book, q — **+ domain (recipes\|herbalism) [to add]** | original_text + normalized + ingredients (linked to Plant) |
| **Passages** (`sections_v1`, `POST /api/search`) | hybrid dense+sparse semantic search | section prose not captured in structured facts |
| **Compounds** (vocabulary, `GET /api/compounds`) | hierarchy + synonyms; reverse-link "which plants contain X" | definition + plant list |

Unique value = **cross-queries over a controlled vocabulary** + **grounded
historical text with source attribution** — neither obtainable by just asking a
model.

## The toolset

### Discovery tier (funnel; returns lists / vocabulary)

1. **`search_plants(q?, compound?, action?, indication?, family?, toxic?, edibility?, kingdom?)`**
   → list of cards (id, name, name_latin, family, is_toxic, kingdom, parts_used,
   uses_count, photo_url, photo_attribution). The headline differentiator:
   structured facet filtering ("non-toxic Lamiaceae with sedative action").
   **Serves fungi too** (`kingdom=гриб`) — the tool description MUST say
   "plants & fungi" so an agent knows to look here for mushrooms.

2. **`search_recipes(q?, category?, book?, domain?)`** → id, name, category, book,
   year + text. `domain` separates culinary (`recipes`) from medicinal
   preparations (`herbalism`: отвары/настои/сборы). Requires adding a `domain`
   filter to `GET /api/recipes` (currently category/book/q only).

3. **`semantic_search(query, collection?, limit?)`** → hybrid search across
   recipes/plants/sections; ranked hits (id, collection, score, payload). Catches
   free-text prose that isn't in structured fields.

4. **`list_vocabulary(kind)`** → controlled-vocabulary values with plant counts:
   medicinal `actions`, `compound_groups`, `families`, `edibility`, `kingdom`.
   Critical for agent ergonomics — it tells the agent the *legal* filter values
   (that "сердечные гликозиды" or "седативное" exist) so it can form valid
   structured queries instead of guessing. Wraps `GET /api/plants/facets`.

5. **`find_compound(q?)`** → compound vocabulary list (name, latin, class,
   synonyms, hierarchy, linked_facts count). Wraps `GET /api/compounds`.

6. **`get_compound(id)`** → vocabulary term + reverse list of plants that contain
   it (id, name, parts, raw names) + hierarchy. Sits in discovery (no verbatim
   source prose), widening the funnel: "what contains tannins" is cheap; reading
   each plant's monograph is the paid `get_plant`.
   **POPULATED (verified 2026-06-04):** Георгиевский ingested successfully on a
   re-run — `Compound` vocab = **715 terms** (гликозиды/антрахиноны/флавоноиды/
   кумарины…), **6100 of 9107** `PlantCompound` rows normalized (`compound_id`
   set). The killer query "which plants contain cardiac glycosides" resolves. The
   earlier source_shapes note ("0 compounds") is STALE.

### Full tier (the product; paid + quota when monetization lands)

7. **`get_plant(id)`** → full monograph: all medicinal/culinary uses with
   `original_text`, compounds, harvests, habitats, toxicities, mentions, linked
   recipes, per-fact source attribution + year, iNat photo (url, attribution,
   license, source, taxon_id). The core product. Serves fungi monographs too
   (fungal parts шляпка/ножка/мякоть/гименофор/плодовое тело; edibility from
   culinary_uses).

8. **`get_recipe(id)`** → full original_text + normalized text + ingredients with
   plant links.

### Live-enrichment tier (iNaturalist passthrough — separate nature)

9. **`find_observations_nearby(plant_id | taxon_id, lat, lng, radius_km?)`** →
   live iNat observations of this taxon near a coordinate (geo "find nearby").
   NOT corpus retrieval — a live passthrough to the iNat API, with iNat's own
   rate limits + attribution. We already store `inat_taxon_id` per plant, so the
   bridge is free. **Agreed to include** (2026-06-04). Resale concern is moot
   pre-monetization; revisit attribution/ToS when the paid gate is wired.
   Photos are already covered — they flow into `search_plants` summaries and
   `get_plant` monographs from the stored enrichment; this tool adds only the
   geo/observation dimension on top.

## Tier → tool map (metering)

| Tier | Tools |
|---|---|
| Discovery (free funnel) | search_plants, search_recipes, semantic_search, list_vocabulary, find_compound, get_compound |
| Full (paid, quota'd) | get_plant, get_recipe |
| Live enrichment (iNat) | find_observations_nearby |

Anti-exfiltration rule: a `search_*` returns a list of hits; the full grounded
`original_text` of a *specific* entity comes only from a metered `get_*`. This is
the bulk-dump control once metering is on.

## What is deliberately NOT a tool

- Write / ingest / pipeline / admin endpoints — admin contour, not for consumers.
- Anything that hosts or proxies an LLM.

## New-feature coverage audit (2026-06-04)

- **Fungi** — fully covered via the `kingdom` facet in `search_plants` /
  `list_vocabulary` and the shared `get_plant` monograph. Only action item is
  documentation: name the tool "plants & fungi".
- **Compounds** — BOTH layers live: free-text (`search_plants(compound=)`) and
  the normalized vocab (`find_compound`/`get_compound`, 715 terms, 6100 facts
  linked) after the successful Георгиевский re-run.
- **iNat photos** — already in both tiers from stored enrichment, nothing to add.
- **iNat geo** — added as tool #9 (live-enrichment tier).
- **Culinary vs medicinal** — split via the new `domain` filter on
  `search_recipes` (herbalism books also yield medicinal recipes).

## Work items

1. **`mcp` docker service** behind nginx (Streamable HTTP transport).
2. Wrap the 9 tools over the existing service/query layer (reuse the router query
   functions, don't duplicate SQL).
3. Add `domain` filter to `GET /api/recipes` (+ the `search_recipes` tool param).
4. Tool descriptions: state "plants & fungi" on `search_plants`.
5. `find_observations_nearby`: thin iNat `/observations` client keyed on stored
   `inat_taxon_id`, with iNat attribution in the payload.
6. **Metering layer is dormant** — design the `api_keys` / `api_usage` recording
   hooks now (provider-agnostic), but do not gate access until monetization.

## Dependencies / blockers

- None outstanding. (The former compound-vocabulary blocker is cleared:
  Георгиевский ingested, 715 terms / 6100 facts linked, verified 2026-06-04.)

## Open questions

- Transport: remote Streamable HTTP behind the existing nginx — confirm at build
  time (matches the planned `mcp` service in the monetization design).
- `find_observations_nearby` input: accept `plant_id` (we resolve taxon) vs raw
  `taxon_id` vs both — lean both, plant_id is the agent-friendly path.
- Do we expose a `get_book` / source-provenance tool, or is the per-fact source
  attribution embedded in every payload enough? (Lean: embedded is enough for v1.)
