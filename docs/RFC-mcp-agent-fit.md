# RFC: agent-fit output for the MCP retrieval tools

**Author (consumer):** nastoiki.pro conversational agent — the *first real consumer*
of the MCP tool surface.
**Owner (author):** HR backend (`backend/app/routers/*`, `backend/app/mcp/server.py`).
**Status:** v1 implemented 2026-07-02 (`view=agent`, search `limit`).

## Problem

Building the nastoiki.pro agent, we measured what the tools actually return for the
standard questions we ourselves put on the site's suggestion chips. The retrieval
tools were built for the web/admin UI and the low-bandwidth field client, not for an
LLM tool-loop. Consequences, measured on prod:

| Tool call | Payload | Why it breaks the agent |
|---|---|---|
| `get_plant` (default) | **1 400 760 bytes** (1062 medicinal_uses, 776 compounds, 227 mentions) | Cannot fit an LLM context; forced consumer-side truncation produced *broken JSON*, starving the model of grounding and pushing it to call more tools. |
| `get_plant?view=field` | 52 KB | Much better (deduped/ranked), but still carries a 116-item `sources` dump + monograph-pipeline internals (`model`, `reviewed`, `generated_from_hash`, `*_total`) that are pure noise for an agent. |
| `search_plants` | up to **113 KB** | The wrapper never passed a `limit`, though the endpoint supports one; each card also drags `names_historical` (100+ items) and photo provenance. |
| `search_recipes` | 11 KB | Same — no `limit` passed. |

Observed effect: for a chip query like *«Расскажи рецепт английской горькой»* the loop
burned 4–5 sequential 235B rounds and returned **empty** — slow and broken.

## Principle

We fix the **tools** until they fit the task, rather than adapting their output on the
consumer side. Consumer-side truncation is a smell; the tool should emit a compact,
valid, sufficient result by design.

## Changes (v1)

1. **`get_plant?view=agent`** (`backend/app/routers/plants.py`). Reuses the existing
   `_field_view` aggregation, then `_agent_slim` drops the source dump + pipeline
   internals + photo provenance and caps fact lists to 8. Result ≈ 20–25 KB of valid,
   fully source-grounded JSON (each fact keeps its own inline source + year). Default
   and `?view=field` are byte-for-byte untouched.
2. **`search_plants` / `search_recipes` / `plants_for_condition`** wrappers pass
   `limit` (agent uses 8). The endpoints already supported it.
3. The agent's consumer-side `_cap` is demoted to a high defensive backstop (60 KB) —
   it should never trip once the tools are agent-fit; if it does, that's a tool bug to
   file here, not a number to tune.

## Backlog (later RFC iterations)

- A lean **search card** (drop `names_historical`/photo fields for the agent) — `limit`
  already keeps it small, so low priority.
- Consider exposing `view=agent` through the MCP `get_plant` tool too (external
  consumers currently get the raw dump), or making it the MCP default.
- Per-fact quote trimming inside `uses` if monographs with many long quotes appear.
- Acceptance target: a standard chip query resolves in ≤ 3 tool rounds with a complete,
  grounded answer.
