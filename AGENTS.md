# AGENTS.md — using the historical-recipes knowledge base

A guide for an LLM agent calling the **historical-recipes** MCP server (and for
contributors maintaining its tool surface). The server is a structured, source-grounded
knowledge base of historical Russian **herbalism, mycology, phytochemistry and recipes**,
built from digitised books. Every fact carries the source book + year it came from.

> Naming note: this file is `AGENTS.md` (uppercase, repo root) — the cross-tool
> convention for agent instructions. The same guidance is summarised in the MCP server's
> `instructions` string (`backend/app/mcp/server.py`); keep the two in sync.

---

## 1. Mental model

Two kinds of things, linked by controlled vocabularies:

- **Entities** — `plant` (plants AND fungi share one herbarium; filter by `kingdom`) and
  `recipe`. These carry the grounded source text.
- **Controlled vocabularies** — three normalized axes the corpus is indexed on:
  - **compound** (phytochemistry: гликозиды, алкалоиды, флавоноиды, рутin…),
  - **action** (medicinal action: мочегонное, седативное, ангиопротекторное…),
  - **indication** (показание — what a remedy is used *for*: кашель, отёки…).

Each vocabulary is **hierarchical** (a class → its members) and has a **synonym** list.
The indication axis additionally has an **archaic → modern bridge**: a 19th-c. term and a
modern one resolve to the same concept (`водянка` → `отёки`, `грудная жаба` → `стенокардия`).
So a query in either register finds the same plants.

The link between chemistry and medicine is **indirect**: the corpus records that a plant
*contains* compound X and, separately, that the plant is *used for* Y. The bridging entity
is always the **plant** — important for the association tool (§4).

---

## 2. The core workflow: discover → search → ground

Tools form a funnel. Don't guess vocabulary values; resolve them first.

1. **Discover** the real vocabulary value (cheap, list-shaped):
   `list_vocabulary`, `find_compound`, `find_indication`.
2. **Search** entities with those values (structured, AND-combining):
   `search_plants`, `plants_for_condition`, `search_recipes`, `semantic_search`.
3. **Ground** the answer by fetching the full source-backed monograph/recipe:
   `get_plant`, `get_recipe`, `get_indication`, `get_compound`.

The grounded `original_text` (with book + year) is the product — always anchor a claim to
it via a `get_*` call rather than answering from a search card alone.

---

## 3. Tool reference

### Discovery — vocabulary & entity search
| Tool | Use it to… | Then chain to |
|---|---|---|
| `list_vocabulary(kind, limit)` | See valid filter values + counts. `kind` = actions \| indications \| compound_groups \| edibility \| kingdom \| all. Call BEFORE a structured `search_plants`. | `search_plants` |
| `find_compound(q, limit)` | Browse the compound vocabulary (substring over name/Latin/class/synonyms), with `linked_facts`. | `get_compound`, `compound_associations` |
| `find_indication(q, system, min_facts, limit)` | Browse indications, incl. the **archaic→modern bridge**. `system=` filters by body system (дыхание, ЖКТ, ССС, ЦНС, кожа, мочеполовая…) — pass it alone to list a whole system. `min_facts` hides long-tail scraps. Sorted by coverage. | `get_indication`, `plants_for_condition` |
| `search_plants(q, compound, action, indication, family, toxic, edibility, kingdom)` | Cross-vocabulary structured search over plants+fungi (all facets AND). E.g. non-toxic Lamiaceae with sedative action. | `get_plant` |
| `plants_for_condition(condition, kingdom, toxic, limit)` | **The medical entry point.** Give a symptom / disease / archaic name / action as a user would say it; it resolves across BOTH the indication and action axes and unions the plants. | `get_plant`, `find_indication` |
| `search_oils(q, limit)` | Search the **essential-oils pillar** (эфирные масла) — separate from the herbarium. Free text over oil/Latin/source-plant name. Each oil is bridged to its source plant. | `get_oil`, `get_plant` |
| `oils_for_condition(condition, limit)` | **Aromatherapy medical entry point** — the oil analog of `plants_for_condition`. Resolves across BOTH indication (archaic→modern bridge) and action axes; cards add `matched_uses`. Aromatherapy evidence is weak — relay as attested usage, not advice. | `get_oil` |
| `search_recipes(q, category, domain, book_id)` | Find recipes. `domain=recipes` (culinary) vs `domain=herbalism` (medicinal preparations) is the only culinary/medicinal split. | `get_recipe` |
| `semantic_search(query, collection, limit)` | Natural-language questions whose answer is in prose, not a field ("чем лечили лихорадку в банях"). Collections: recipes_v2 \| plants_v2 \| sections_v1. | `get_plant`, `get_recipe` |

### Grounding — full source-backed documents
| Tool | Returns |
|---|---|
| `get_plant(plant_id)` | Full monograph: identity + ALL layered facts (uses, compounds, harvests, habitats, toxicity, culinary), each with verbatim `original_text` + book/year, plus cross-linked recipes. |
| `get_recipe(recipe_id)` | Verbatim + normalized text, ingredient list linked to herbarium plants, source author/year. |
| `get_indication(indication_id)` | One concept (modern + archaic names, hierarchy) + the plants that treat it (concept + descendants). |
| `get_compound(compound_id)` | One compound (class, synonyms, hierarchy) + the plants that contain it. |
| `get_oil(oil_id)` | Full essential-oil monograph: identity (name/Latin/synonyms), source-plant bridge, part/extraction/aroma_profile/description, and ALL aromatherapy use-facts (normalized action + indication concepts, application, dosage, contraindications) each with verbatim `original_text`. |

### Analysis — chemistry ↔ medicine association  (§4)
| Tool | Use it to… |
|---|---|
| `compound_associations(compound_id, axis, limit, min_support)` | Hypothesis lead: which indications (`axis=indication`) or actions (`axis=action`) the plants containing a compound tend to treat, ranked by significance. |

### Identification & live data
| Tool | Notes |
|---|---|
| `identify_plant(image_urls, organs, limit)` | Photo URL(s) → candidate species (Pl@ntNet), each linked to our herbarium when present. Chain matched `plant.id` → `get_plant`. Confidence is the external engine's. |
| `find_observations_nearby(plant_id, region \| lat/lng, radius_km, limit)` | Live iNaturalist sightings: commonness (`total_count`), `seasonality` histogram, sample sightings. Prefer `region` (a place name) for "where in X". **External live data — display the iNat attribution.** |

---

## 4. The association tool — read this before using it

`compound_associations` answers the question *"if a plant contains substance X, does it
tend to help with condition Y?"* — but the corpus never states "X treats Y". It records,
separately, that a plant **contains** X and that the plant is **used for** Y. The tool
mines that **co-occurrence through the bridging plant**.

**This is an association, not causation or mechanism.** A plant holds dozens of compounds
and serves dozens of uses; we cannot attribute an effect to one constituent. Treat every
result as a **research lead / hypothesis**, never as medical advice.

How to read a result row:
- **`p_value` first.** Ranking is by a one-sided hypergeometric (Fisher) tail probability:
  the chance of seeing this much overlap if compound and condition were unrelated. Small =
  unlikely by chance.
- **`lift` is secondary.** With few plants per compound, lift balloons on 2-plant
  coincidences. A row with `lift=14, support=2, p_value=0.1` is **weak**; a row with
  `lift=1.5, support=80, p_value≈0` is **strong but unspecific**.
- **`support` / `target_plants`** — how many plants back it (e.g. `4/8`).
- **`plants`** — the supporting plants. **Verify** by reading their `get_plant` monographs.

`axis="action"` (broad medicinal actions) is more robust than `axis="indication"` (specific
conditions) — more support per category, closer to mechanism — so prefer it for a first
look. Querying a compound **class** (e.g. флавоноиды) auto-includes hierarchy descendants
(рутин, кверцетин…) for more statistical power.

Worked example (real output): `compound_associations(<рутин>, axis="action")` ranks
**укрепляющее сосуды** top (p≈0.001, lift 7.1, 4/8 plants) — consistent with rutin's known
capillary-strengthening (P-vitamin) activity — above noisier high-lift/low-support rows.

**Phrasing to a user:** *"Plants containing rutin are often used for vascular-strengthening
(co-occurrence across N plants)"* — never *"rutin cures X"*.

Reverse direction (what chemistry underlies treating a condition) is available as a REST
endpoint, `GET /api/medical/indications/{id}/compounds`, with the same statistics.

---

## 5. Global caveats

- **Historical sources.** Facts come from digitised herbals/lechebniki spanning centuries;
  they reflect what a source *claimed*, not modern clinical evidence. Surface the book +
  year. This is reference/history, **not medical advice**.
- **Provenance is mixed by design.** Compounds come largely from modern phytochemistry
  references; indications partly from historical herbals. The bridge between them (§4) is
  the interesting part — and exactly why its output is a hypothesis, not a fact.
- **Recall over a fragmented vocabulary is fine.** Resolvers union a concept's synonyms,
  archaic names and hierarchy descendants, so you don't need the exact canonical spelling.
- **Live data must be attributed.** iNaturalist (`find_observations_nearby`) and Pl@ntNet
  (`identify_plant`) are external; return and display their attribution.

## 6. For contributors

- MCP tools are **thin REST wrappers** (`backend/app/mcp/server.py`); all query logic lives
  in the FastAPI routers (`backend/app/routers/`). Add capability to a router first, then a
  wrapper. Keep `instructions` and this file in sync when the tool set changes.
- Corpus-wide maintenance: `POST /api/medical/run` (grow + relink the action/indication
  vocabularies), `POST /api/compounds/normalize`, and the **review-first** dedup passes
  `POST /api/medical/dedup`, `/api/medical/dedup-actions`, `/api/compounds/dedup`
  (`apply=false` previews, `apply=true` writes; see `backend/app/services/vocab_dedup.py`).
