# Handoff: `RFC-monograph-deepen-links` — backend status (Part B live, Part A deferred)

**For:** the «Что растёт» Android client agent.
**From:** the `historical-recipes` backend agent.
**Re:** `docs/RFC-monograph-deepen-links.md`.
**Date:** 2026-06-08. Verified against prod (`46.173.19.68:8126`).

---

## TL;DR

**Part B** (the `?view=field` contract additions backed by data we already have) is
**implemented, deployed, and live.** You can build the definition-sheet tap UI now
against the final shape.

**Part A** (the *authored* content — action definitions, compound-definition
backfill/cleanup, `fun_fact`) is **deferred by the product owner** until the whole
corpus is loaded and run through the data-quality «линтер гербария»
(`RFC-data-quality.md`). It is review-gated LLM authoring and will not be
request-time generated. The contract fields for it ship **now as `null`** so nothing
in your code has to change when the data lands later.

---

## What is LIVE now in `GET /api/plants/{id}?view=field`

### `uses[]` gained `action_id`, `action_definition`, `quote`

```jsonc
{
  "action": "обволакивающее",
  "action_id": "855b3e7d-…",         // controlled-vocab concept id, or null (≈65% linked corpus-wide)
  "action_definition": null,          // ALWAYS null for now — see "Deferred" (RFC A1)
  "parts": ["корневище"],
  "indications": ["…"],
  "indication_ids": ["…"],
  "source_count": 2,                  // → your «читать ещё (source_count − 1)» label
  "quote": {
    "text": "Отвар из корневищ используют как обволакивающее средство.",
    "source": "Травник (часть 2) — монографии растений"
  }
}
```

**`quote` selection (server):** from the rows backing that action, we prefer one that
is *actionable* (has preparation/dosage), then one that is cited, then a moderate
length; trimmed to ~240 chars on a word boundary; **ICD/MKB codes stripped** (same
cleanup as `RFC-field-view-data-noise.md`). `quote` is `null` if no row had an
`original_text`.

> ⚠️ **Quote quality is bounded by the source.** Some rows' `original_text` is not
> prose but the structured indications+dosage text, so the quote can read like
> "воспалительные заболевания… (настойка корневищ 1:2… по 10–30 капель)". It's
> code-free and usually carries real dosage info, but it isn't always a clean
> sentence. A cleaner-quote pass is part of the deferred data work.

### `compound_groups[].examples[]` gained `definition`

```jsonc
{ "name": "рутин", "compound_id": "…", "definition": "…" | null }
```

Sourced from the `Compound` vocabulary (`definition` exists for ~57% of vocab rows;
~25% of a plant's compound rows are linked to the vocab, so coverage on any given
plant is partial).

> 🚨 **Do NOT surface compound `definition` to users yet.** Verified live: some
> definitions are **wrong** — e.g. `дубильные вещества` →
> *"…вещества, содержащиеся в соцветиях бессмертника песчаного"* (a stray
> plant-specific scrap, not what tannins are). This is exactly the A2 defect in your
> RFC. The field is wired so you can **build and test** the compound sheet, but
> please gate its *display* until the A2 cleanup lands. We'll add a `verified`
> signal when A2 runs so you can switch on display safely.

### top-level `fun_fact`

```jsonc
"fun_fact": null            // ALWAYS null for now — see "Deferred" (RFC A3)
```

### Unchanged / safe

- The **default** `GET /api/plants/{id}` (no `?view=field`) and the MCP `get_plant`
  tool are **byte-for-byte unchanged**. All of the above is field-view only.
- All new fields are nullable/optional — keep omitting a block when it's absent.

---

## Interop note for «читать ещё (N)» — important

Your plan (per the RFC) is to lazily fetch the remaining quotes by reusing the
**default** `GET /api/plants/{id}` and filtering its `medicinal_uses[]` by action.
One gotcha:

- The **field view splits** a multi-action blob (`"диуретический, анальгетический,
  противовоспалительный"`) into **separate** `uses` rows, one per action.
- The **default view does NOT split** — that same source row still has
  `action: "диуретический, анальгетический, противовоспалительный"` (or `action_raw`).

So when filtering default `medicinal_uses` for the tapped action, **match rows whose
action _contains_ the tapped action** (case-insensitive substring), not an exact
equality — otherwise a split action will find zero quotes. Each default row carries
`original_text` + `source`, which is what you render in the expanded list.

---

## Deferred to the data-load + clean phase (do not expect these soon)

Product decision (2026-06-08): **no complex/authored generation until the corpus is
fully loaded and cleaned.** When that phase runs (folded into `RFC-data-quality.md`):

| RFC item | What lands | Effect on this contract |
|---|---|---|
| **A1** actions vocabulary + definitions | `medicinal_actions` consolidated (it's 870 rows today, fragmented) + a `definition` column authored, + better `action_id` linkage | `uses[].action_definition` starts returning text; more `action_id`s non-null |
| **A2** compound-def backfill + cleanup | fix the wrong defs, fill gaps, add a `verified` signal | `examples[].definition` becomes trustworthy → safe to display |
| **A3** «Интересное» | grounded, **sourced** per-plant `fun_fact` | `fun_fact` starts returning `{text, source}` |

None of these change the JSON **shape** — they only flip `null`→value. Build against
the shape above and you're done; the deferred data will populate in place.

---

## Quick test

```
# field view (new fields)
curl -s "http://46.173.19.68:8126/api/plants/0e68696f-deb0-4b3e-82ac-ea80e3cb3874?view=field" | jq '.uses[0], .fun_fact'
# default view (unchanged)
curl -s "http://46.173.19.68:8126/api/plants/0e68696f-deb0-4b3e-82ac-ea80e3cb3874" | jq 'has("uses")'   # → false
```
