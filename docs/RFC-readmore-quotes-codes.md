# RFC: «читать ещё» quotes leak ICD/MKB codes — clean the expanded quote path

Status: **Proposed** · Author: «Что растёт» Android client agent · Created: 2026-06-08

> Follow-up to `RFC-monograph-deepen-links.md` (deepen-in-place taps) and
> `HANDOFF-deepen-links-backend-status.md` (Part B live). Companion to
> `RFC-field-view-data-noise.md` — this is the **same ICD/MKB-code class of
> defect**, resurfacing on a path that the earlier fix didn't cover.

## Symptom (live, verified by the product owner 2026-06-08)

In the action tap sheet:

- The **inline `quote`** (the one representative quote embedded in
  `?view=field`) reads **clean** — codes stripped server-side, as designed.
- Tapping **«читать ещё (N)»** reveals the remaining quotes **with the ICD/MKB
  codes back in** — e.g. `…язвенная болезнь (К25`, `дуоденит (К29.8)`,
  `болезни органов дыхания (J00-J47`.

Clean primary + dirty expansion in the *same sheet* reads as broken and undoes
the credibility win of the cleanup.

## Root cause — the lazy path reuses the **unchanged** default endpoint

Per `RFC-monograph-deepen-links` Part B and your handoff, «читать ещё» is
implemented client-side by fetching the **default** `GET /api/plants/{id}` and
filtering its `medicinal_uses[].original_text` by action. Your handoff is
explicit that this endpoint is **byte-for-byte unchanged** (shared with MCP
`get_plant`) — so its `original_text` still carries the raw codes. Only the
**field view** runs the code-stripping. The inline quote goes through the
cleaner; the expanded quotes never do.

So the inconsistency is structural, not a bug in either endpoint on its own.

## The client should NOT fix this

By standing agreement (see `RFC-field-view-data-noise.md`), the client does not
mask classifier codes on the device — that's data/aggregator work, and a
client regex would silently diverge from the canonical stripping rules. We keep
rendering exactly what the API sends.

## Proposed fix — embed ALL cleaned quotes for the action in field view

The cleanest resolution also **removes a network round-trip in the forest**,
which is strictly better for a weak-signal field app:

Instead of *one* inline `quote` + a lazy fetch of the rest from the dirty
default endpoint, have `?view=field` embed the **full, already-cleaned** quote
list for each action inline:

```jsonc
// uses[] — replace single `quote` with the full cleaned set
{
  "action": "вяжущее",
  "action_id": "…",
  "parts": ["трава"],
  "indications": ["раны", "поносы"],
  "indication_ids": ["…"],
  "source_count": 7,
  "quotes": [                          // ALL rows backing this action, code-stripped
    { "text": "…", "source": "Носаль 1960" },   // [0] = the representative one (ranked first)
    { "text": "…", "source": "…" },
    …
  ]
}
```

- Each quote runs through the **same** stripping the inline `quote` already uses
  (codes removed, ~240-char word-boundary trim).
- `quotes[0]` = the current representative quote (actionable + cited first), so
  the sheet shows `quotes[0]` by default and «читать ещё (N−1)» just expands the
  rest of the **same already-loaded** array — **no second request, no dirty
  data, no forest round-trip.**
- Payload cost is small: quotes are short (240-char cap) and `source_count` is
  typically 2–7. This is well within the «embed, no field round-trips» principle
  the deepen-links RFC already adopted for definitions.

### Alternative (if embedding all is unwelcome)

Keep the lazy path but serve it from a **field-flavored** accessor that applies
the same stripping — e.g. `GET /api/plants/{id}?view=field&quotes_for=<action>`
returning the cleaned remaining rows. This preserves the one-inline + lazy shape
but routes the expansion through the cleaner. Costs a round-trip; embedding does
not. **Embedding is preferred.**

## Client change once this lands

Trivial and forward-compatible:

- Parse `uses[].quotes[]` (list of `{text, source}`).
- Render `quotes[0]` inline; «читать ещё (N)» reveals `quotes[1..]` from memory.
- **Delete** `Api.useQuotes()` and its call against the default endpoint — the
  dirty path goes away entirely.
- If `quotes` is absent (old payload), fall back to the current single `quote` +
  lazy path, so this ships without lockstep deploys.

## Scope / non-goals

- Default `GET /plants/{id}` and MCP `get_plant` stay **unchanged** — we do not
  ask you to strip codes there; the fix lives in field view only.
- This is the same `RFC-field-view-data-noise` cleanup applied to one more
  field; no new stripping logic, just covering the expanded-quote rows.
