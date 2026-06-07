# Handoff: server-side aggregation for the plant monograph (field client)

**For:** the agent maintaining `historical-recipes` (backend).
**From:** the agent building the «Что растёт» Android client (`poker26/chto-rastet-android`).
**Status:** spec only — design agreed with the product owner. No backend code written yet.
**Scope:** Phase 1, deterministic aggregation only. **No LLM / no summarization** in this
phase (that's a possible Phase 2, explicitly out of scope here).

---

## Why

The app's monograph screen (`GET /api/plants/{id}`) is currently a faithful DB dump.
That's fine for the median plant (**2** medicinal rows, **1** compound), but the plants a
forager actually photographs are the long tail:

| plant | `medicinal_uses` rows | `compounds` rows |
|---|---|---|
| Календула | 125 | 80 |
| Чистотел | 124 | 163 |
| Мята | 122 | 64 |
| corpus p90 | 18 | 23 | (max 125 / 163)

Tapping Календула returns ~125 rows like «противовоспалительное — … — настой [Носаль 1960]»,
many near-duplicates of the same action across different source books, in ingestion order.
On a phone, on a weak field connection, this is both **unreadable** and a **fat payload**.

**Two goals, both served by the same change:**
1. **UX** — show the few things that matter, ranked, with depth on demand.
2. **Payload** — the client has poor connectivity (this is a field app). Dedup collapses
   125 rows → ~15–25 distinct actions and 163 compounds → ~10 groups, so the *whole* compact
   monograph fits in one small response. **No "load more" round-trips** — the client expands
   locally from data it already has.

The product decision: **do the heavy lifting server-side, keep the client thin.**

---

## The contract

Add an opt-in **`?view=field`** query param to `GET /api/plants/{id}`.

- **Default response (no param) stays EXACTLY as today** — the MCP `get_plant` tool and any
  other consumer are untouched. No back-compat risk.
- **`?view=field`** returns the compact, aggregated shape below. Only the app sends it.

Identity fields are unchanged (`id`, `name`, `name_modern`, `name_latin`, `family`,
`kingdom`, `is_toxic`, `photo_url`, `photo_attribution`, `description`, `parts_used`).
Everything below **replaces** the raw `medicinal_uses` / `compounds` / `toxicities` /
`culinary_uses` / `harvests` / `habitats` arrays in this view.

```jsonc
{
  // …identity fields as today…

  "roles": ["medicinal", "edible", "toxic"],   // derived flags, for the verdict badges
  // medicinal = has medicinal_uses; edible = has culinary_uses; toxic = is_toxic

  // ── «Главное»: deduped + ranked medicinal actions ───────────────────────────
  // Dedup key = controlled-vocab action.name when present, else normalized action_raw.
  // Rank by source_count DESC (how many source rows/books back it — a cheap consensus
  // proxy), tie-break by max(confidence). Send the FULL deduped list (already small);
  // the client shows the first ~5 and expands the rest locally.
  "uses": [
    {
      "action": "противовоспалительное",
      "parts": ["цветки", "трава"],            // distinct, merged across the collapsed rows
      "indications": ["раны", "ангина", "ожоги"], // distinct free-text, capped to ~6
      "indication_ids": ["<uuid>", "..."],     // distinct controlled-vocab ids (tappable concepts)
      "source_count": 7                          // # of collapsed rows / books supporting it
    }
    // …~15–25 of these for a well-covered plant…
  ],

  // ── Safety, always structured (never only in prose) ─────────────────────────
  "cautions": {
    "contraindications": ["беременность", "гипотония"],  // distinct, merged from medicinal_uses
    "toxic_parts": ["корень"],                            // from toxicities
    "symptoms": "тошнота, рвота",                         // deduped/joined from toxicities
    "antidote": "…"                                       // if present
  },

  // ── Chemistry, grouped (163 rows → ~10 groups) ──────────────────────────────
  "compound_groups": [
    {
      "group": "флавоноиды",
      "examples": [                              // capped to ~6 per group
        { "name": "рутин", "compound_id": "<uuid|null>" },
        { "name": "кверцетин", "compound_id": "<uuid|null>" }
      ],
      "count": 12                                // total compounds in this group
    }
    // ungrouped compounds → a single {"group": null, …} bucket
  ],

  // ── Forager context (we have it, we never showed it) ────────────────────────
  "harvest": {
    "parts": ["цветки"],          // distinct from harvests
    "seasons": ["июнь–август"],   // distinct
    "where": ["опушки", "луга"]   // distinct biotopes from habitats
  },

  // ── Edible use, compacted ───────────────────────────────────────────────────
  "culinary": [
    { "use": "салаты, чай", "part": "листья", "season": "весна", "caution": null }
  ],

  // ── Recipes: refs are tiny, send all (or cap to ~20 + total) ────────────────
  "recipes": [ { "id": "<uuid>", "label": "Настой при простуде — Носаль 1960" } ],

  // ── Credibility footer ──────────────────────────────────────────────────────
  "sources": ["Носаль 1960", "Анищенко 1980", "…"]   // distinct book citations
}
```

### Aggregation rules in one place
- **`uses`** — group by action key; merge `parts` / `indications` / `indication_ids`
  (distinct); `source_count` = collapsed row count; sort by `source_count` desc, then
  `max(confidence)` desc. Indications capped ~6 (keep the ones from the most rows).
- **`compound_groups`** — group by `compound_group`; `examples` = distinct compound names
  capped ~6 (prefer those with a `compound_id` so they stay tappable); `count` = group size;
  null group → one trailing "прочие" bucket. Order groups by `count` desc.
- **`cautions`** — union of `contraindications` across medicinal_uses + the toxicities block.
- **`harvest`** — distinct merge of harvests.season/part + habitats.biotope.
- Everything **distinct + capped** so the payload is bounded regardless of source count.

### Payload target
A well-covered plant should drop from tens of rows / ~50–100 KB to a single **~5–15 KB**
response. That's the whole point for the weak-connection field case.

---

## What the client will do (FYI, my side — so you know the intent)

Thin render, no aggregation, no extra requests:
1. Photo + modern name + latin.
2. Verdict badges from `roles` + `is_toxic` (☠️ ядовито / 🍽 съедобно / 💊 лекарственное),
   family, `parts_used`.
3. `description` as the one-paragraph "what it is".
4. **«Главное»**: first ~5 `uses` (action + a couple indications), `cautions` surfaced
   prominently. "Показать все" expands the rest of `uses` **locally** (already in payload).
5. Collapsible: full uses, `compound_groups` (top examples + "ещё N"), `harvest`, `culinary`,
   `recipes`, `sources`.

If any block is absent/empty the client just omits it — please keep fields nullable/optional.

---

## Notes / decisions
- **No LLM here.** Pure SQL/Python aggregation: deterministic, zero added latency, zero
  hallucination risk (relevant given the project's extraction-hallucination history). A
  grounded, *precomputed-and-cached* `summary_short` is a possible Phase 2 — NOT this handoff,
  and even then it must never be the only place safety info lives, and must never be generated
  at request time (the client is offline-ish).
- **Param, not replacement**, specifically to keep MCP `get_plant` and its raw shape intact.
- Ranking by `source_count` is a deliberate, cheap "consensus across digitized books" proxy
  for importance — good enough for Phase 1; no scoring model needed.
- Open question for you: whether to compute on the fly (fine — it's one already-loaded plant's
  relations) or memoize. Given monographs are opened one at a time, on-the-fly is likely fine.
```
