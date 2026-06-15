# RFC: Data-quality / consistency-checking system («линтер гербария»)

Status: **Draft** · Author: pipeline notes · Created: 2026-06-07

## Summary

We have ~5k plants, ~12.7k recipes and a growing oils pillar assembled from 201
OCR'd books by an LLM pipeline. The data is good enough to be useful and bad
enough to embarrass us: a Physalis card titled **«Жидовская вишня» with latin
`Prunus cerasus`**, a **черешня (`Prunus avium`)** masquerading as «Вишня
обыкновенная», and a wild steppe cherry (`Prunus fruticosa`) that had captured
**308 ordinary-cherry recipes** because a bare `Вишня` sat in its
`names_historical` and the matcher latched onto it. Each was found and fixed by
hand, one card at a time, with a throwaway script.

That does not scale and does not *stay* fixed. This RFC proposes a standing
mechanism: a **registry of validators that emit structured findings into a
queryable table**, a **review queue** to triage them, **auto-fix** for the safe
classes and **human-confirm** for identity, plus a **publish gate** so unchecked
absurdity never reaches end users (MCP / public).

The plan is to run a full diagnostic sweep once the current corpus finishes
processing, **pause new-book acquisition**, size the problem by real numbers,
then burn down the backlog and leave the per-book gate active so regressions
can't creep back.

## Motivation

The failures we keep hitting are not random — they form *classes*, and a class
is checkable:

- **Identity incoherence** — Russian name, latin binomial and `kingdom` that
  don't describe the same organism (the «жидовская вишня» class).
- **Alias landmines** — a `names_historical` entry that is a bare common word
  also used as another species' primary name, silently mis-routing the matcher
  (the `Вишня`-on-steppe-cherry case; root cause of the recipe scramble).
- **OCR-mangled latin** — `M и r a b и l и s J a l a p a L.`, `Sect. Rіbes I.`
  (Cyrillic `і`), author-citation noise — unresolvable binomials.
- **Stubs** — cards/oils with zero facts and only a TOC-scrape mention (the
  ~46 name-only oil stubs; the empty `36bf1679`).
- **Index drift** — orphaned `plants_v2` points for deleted plants; missing
  points for live ones (we just reconciled cherry orphans by hand).
- **Ungrounded facts** — the extract_recipes hallucination (10 fabricated
  recipes in Анищенко 1980); the model recites from memory.

Each is a one-off fire today. The leverage is to make *finding* them a
first-class, repeatable system — the analog of a linter, not a debugger.

## The mechanism: findings registry, not scripts

A one-shot script finds, fixes and forgets. Instead we want a **standing suite**
whose product is durable, trackable findings.

### Model — `data_quality_findings`

```
id
check_id            # e.g. "identity.name_vs_latin", "alias.collision"
severity            # P0 / P1 / P2  (see tiers)
entity_type         # plant | recipe | oil | identification | book | qdrant
entity_id
title               # human one-liner
evidence            # JSONB: what we observed (our value vs external truth, etc.)
suggested_fix       # JSONB: structured, machine-applicable where possible
auto_fixable        # bool
status              # open | confirmed | dismissed | fixed | stale
first_seen / last_seen
resolved_by / resolved_at / note
```

Why a table, not a report:
- **Dedup + trend.** A finding persists across runs; we see «стало лучше/хуже»,
  not a fresh wall of text each sweep.
- **Triage state.** A human `dismiss` (false positive) sticks — the next sweep
  doesn't re-surface it.
- **Auditability.** Every fix is attributable; identity merges leave a trail.

### Validators

A `Validator` is a registered callable: `run(db) -> list[Finding]`. Each owns one
`check_id`, declares its severity and `auto_fixable`, and is pure-read (emits
findings, never mutates). Fixing is a separate, explicit step (auto or human).
The registry makes the catalogue extensible — adding a check is adding one
function, like adding a lint rule.

### Run modes

- **Full sweep** — a Temporal workflow (`DataQualitySweepWorkflow`) iterating the
  registry over the whole corpus, on a schedule + on demand. Mirrors the existing
  `InatEnrichmentWorkflow` durability pattern.
- **Incremental** — a new `validate` step appended to the book pipeline
  (`PIPELINE_STEPS_*` in `workflows.py`, after `index`), scoped to that book's
  entities. New books get checked at ingest; regressions are caught at the door.

### Severity tiers

- **P0 — user-facing absurdity.** name↔latin mismatch, kingdom mismatch, alias
  collision, OCR-garbage latin. **Gates publication.**
- **P1 — likely-wrong / missing.** unresolvable latin, zero-fact stub, missing
  photo for a resolvable taxon, low-confidence recipe link.
- **P2 — cosmetic / dedup.** casing, author-citation normalization, whitespace,
  near-duplicate names.

### Publish gate

The MCP / public read paths already tier data depth. Add a quality predicate:
an entity with an **open P0** finding is held in an internal `review` state and
excluded from public/MCP responses until confirmed or fixed. This is the direct
answer to «чтобы пользователи не приходили в шок от жидовских вишень» — we keep
seeing everything internally; only the clean set goes out.

## The check catalogue

Grouped by dimension. `auto?` = safe to auto-fix vs needs human confirm.

### 1. Identity coherence (the «жидовская вишня» class) — highest value

- **`identity.name_vs_latin`** — resolve `name_latin` against an external
  taxonomy (GBIF / POWO / iNaturalist — already wired via the trusttunnel proxy;
  MycoBank for fungi). Pull accepted name + RU vernaculars + kingdom. Flag when
  our `name` has no relation to the taxon's accepted vernaculars/synonyms.
  *human.* (Catches Physalis-as-`Prunus cerasus` instantly.)
- **`identity.kingdom`** — latin genus → kingdom per taxonomy; compare to our
  `kingdom` (`растение`/`гриб`). *human.*
- **`identity.latin_unresolvable`** — `name_latin` doesn't parse as a binomial
  or doesn't resolve to an accepted taxon. Catches OCR garbage. *P1, human.*
- **`identity.latin_dupe`** — multiple cards share a `_latin_key`. We already
  have `merge_plants_by_latin_key` (`POST /api/plants/dedupe-latin`); wrap its
  output as findings instead of running it blind. *auto (existing merge).*
- **`alias.collision`** — a `names_historical` entry equal (normalized) to
  another plant's *primary* `name`. This is the matcher landmine; the single
  highest-leverage check for recipe-routing sanity. *P0, human (strip vs keep).*

### 2. Cross-reference consistency (exploit the relational graph)

- **`xref.recipe_link_confidence`** — score each `RecipeIngredient.plant_id` /
  `Ingredient.plant_id` link: ingredient string vs linked plant's name+aliases.
  Low-confidence → queue. Would have flagged steppe-cherry's 308. *P1, human.*
- **`xref.oil_source`** — `EssentialOil.plant_id` null, or genus mismatch
  between oil name and source plant. *P1.*
- **`xref.compound_unlinked`** — `PlantCompound.compound_id` null where a vocab
  term exists (dovetails with RFC-reference-normalizer). *P2, auto.*
- **`xref.photo_vs_identity`** — we fetch iNat photos *by latin*, so the photo's
  taxon must equal the card's latin. Mismatch, or missing photo for a resolvable
  taxon. *P1.*

### 3. Grounding / provenance

- **`grounding.fact_in_source`** — sampled LLM audit: does `original_text`
  actually contain the extracted claim? Generalizes the extract_recipes
  grounding audit to all fact types. *P1, human.*
- **`stub.zero_facts`** — plant/oil with 0 child facts and only TOC-scrape
  mentions. Queue for merge/purge. *P1.* (the ~46 oil stubs, empty `36bf1679`.)

### 4. Domain / classification

- **`domain.book_content_mismatch`** — book classified `herbalism` but yields
  only recipes; a cookbook producing «лекарственные применения». *P2, human.*

### 5. Index hygiene (Postgres ↔ Qdrant)

- **`index.qdrant_drift`** — every live plant has exactly one `plants_v2` point;
  flag orphan points (deleted plants) and missing points. *auto-reconcile.* (we
  just did this by hand for the cherry orphans.)

### 6. Normalization / formatting

- **`norm.mixed_script`** — Cyrillic/Latin char mixing in `name_latin`
  (`Rіbes`, `M и r a b и l и s`). *P1, human (it signals OCR damage).*
- **`norm.latin_citation`** — author-citation / casing / whitespace cleanup.
  *P2, auto.*

## Reuse: we already have the bricks

This is largely **composition of existing tools into a standing suite + findings
store + review queue**, not greenfield:

- `merge_plants_by_latin_key` (`/api/plants/dedupe-latin`) — latin-key merge.
- `enrich_plants_inat` (`/api/plants/enrich-inat`) — taxon resolution + photo,
  the external-truth backbone for identity checks.
- `relink_recipe_ingredients` (`/api/plants/relink-recipes`) — the matcher; its
  confidence scoring powers `xref.recipe_link_confidence`. **NB:** it does a
  destructive global recompute and will re-capture aliases — so `alias.collision`
  must be cleaned *before* any corpus-wide relink, or it undoes the fix.
- grounding guard (shipped 2026-06-03) — basis for `grounding.fact_in_source`.

## Options

- **A (recommended):** build the **findings registry + validator suite + review
  UI + publish gate** as one subsystem; seed it with the P0 identity checks
  (`name_vs_latin`, `kingdom`, `alias.collision`) which cover the cases that
  actually shipped. More upfront work; pays back every book hereafter and gives
  us the publish gate.
- **B (lighter):** a standalone «sweep» script that prints a report each run.
  Cheap, but no triage state, no dedup, no gate — we re-litigate false positives
  every run and nothing stops bad data reaching users. Same trap as the
  per-card scripts, one level up.

Recommendation: **A.** The whole point is that these are recurring *classes*; a
report is a debugger, the registry is a linter. Start narrow (the three P0
identity checks) and grow the catalogue one validator at a time.

## Work items (Option A)

1. **Model + migration:** `data_quality_findings` table (additive, nullable).
2. **Framework:** `Validator` registry; a `Finding` dataclass; an upsert that
   dedups on `(check_id, entity_id)` and ages unseen findings to `stale`.
3. **Sweep:** `DataQualitySweepWorkflow` (Temporal) + manual
   `POST /api/quality/sweep`; mirror `run_enrich_inat` durability.
4. **First validators (P0):** `identity.name_vs_latin`, `identity.kingdom`,
   `alias.collision`, `index.qdrant_drift`. These cover everything that bit us.
5. **External-truth client:** a thin taxonomy resolver (GBIF/POWO/iNat via the
   trusttunnel proxy; MycoBank for fungi) returning accepted name + RU
   vernaculars + kingdom, cached.
6. **Review UI:** an admin page listing findings (filter by check/severity/
   status) with confirm / dismiss / apply-fix; reuse the existing admin-page
   shell.
7. **Auto-fix executors:** for `auto_fixable` checks (dedupe-latin merge,
   qdrant reconcile, citation normalize) — explicit, logged, reversible-where-
   possible.
8. **Publish gate:** quality predicate on the MCP / public read paths; hold
   entities with open P0 in `review`.
9. **Per-book `validate` step:** append to `PIPELINE_STEPS_*` after `index`,
   scoped to the book's entities.

## Process (when the current corpus is done)

1. Finish the in-flight batch; **pause acquisition** of new books.
2. Run the **full diagnostic sweep**, read-only — count findings per check
   (how many name↔latin mismatches, OCR-garbage latin, zero-fact stubs, qdrant
   orphans, alias collisions). **Size the problem by real numbers before
   prioritizing.**
3. Burn down P0, then P1; auto-fix the safe classes, triage identity by hand.
4. Turn on the publish gate + per-book `validate`; **resume acquisition** with
   the gate active so new books can't reintroduce the same classes.

## Relation to the other RFCs

- **RFC-reference-normalizer** builds *controlled vocabularies* (compounds,
  actions). This RFC *validates entities against external truth* and *normalizes
  identity*. They share the relink/normalize discipline and the grounding guard;
  `xref.compound_unlinked` is the seam between them.
- **RFC-fungi** introduced the `kingdom` tag; `identity.kingdom` is the check
  that keeps it honest (растение vs гриб not confused).
- **HANDOFF-plant-id-field-features** / iNat enrichment is the external-truth
  source this RFC's identity checks depend on.

## Open questions

- **Auto vs human boundary.** Lean: auto only for mechanically-certain classes
  (latin-key merge, qdrant reconcile, citation cleanup). Anything touching
  *identity* (name/latin/kingdom) is human-confirm — a wrong auto-merge is worse
  than a flagged stub (same philosophy as the deduper's ambiguity guard).
- **External taxonomy of record.** GBIF (broad, has an API) vs POWO (botanical
  authority) vs iNat (already wired, has RU vernaculars). Lean: iNat as primary
  (RU names + existing integration), GBIF as the accepted-name/kingdom backbone,
  MycoBank for fungi.
- **Gate strictness.** Hard-hide P0 from users, or show with a «проверяется»
  badge? Lean: hard-hide for P0 identity; badge for P1.
- **Confidence threshold for recipe links.** Needs calibration against the
  sweep's real distribution — defer the number until step 2.
