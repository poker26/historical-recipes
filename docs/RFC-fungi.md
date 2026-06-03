# RFC: Ingesting fungi (mushroom guides) into the knowledge base

Status: **Implemented (Option A)** (2026-06-03) · Author: pipeline notes · Created: 2026-06-03

> Shipped Option A: a `kingdom` discriminator (`растение | гриб`) on `Plant`
> (migration `007_kingdom`, server-default backfills existing rows to растение),
> the «гриб» token family demoted to a part/stop word in `plant_matching.py`
> (verified: «белый гриб»/«польский гриб»/bare «грибы» seed no tier-2 key, while
> distinctive species like «подберёзовик» still do), a fungal `_PART_CANON`
> extension (шляпка/ножка/мякоть/гименофор/плодовое тело), a `fungi` domain that
> reuses the whole herbalism pipeline (`steps_for_domain`) with the extractor
> stamping `kingdom='гриб'` on new rows, `_index_plants` emitting a `kingdom`
> payload + a «Гриб:»/«Растение:» embed label, and `/api/plants` exposure
> (`kingdom` filter + facet + monograph/summary field). Edibility is covered by
> the already-shipped RFC-culinary-uses. The DJVU→PDF blocker (#0) was already
> solved generically at upload. **No fungi book is queued yet** — this is the
> groundwork; load a mushroom guide with `domain=fungi` to exercise it.

## Problem

We want to ingest mushroom field guides / determiners (e.g. Юдин, «Большой
определитель грибов», 2001) into the same knowledge base that today holds
plants. Fungi are a **separate biological kingdom** — not plants — but the
herbalism pipeline models everything as a `Plant` row in the `plants` table and
indexes it into the `plants_v2` Qdrant collection.

Loading a fungi atlas "as-is" through the `herbalism` domain **mechanically
works but is semantically wrong**, and trips several pipeline assumptions tuned
for vascular plants.

### What works out of the box

- Entry splitting: `_HEADER_RE` (`Название – Latin binomial`) matches mushroom
  headers like «Белый гриб – Boletus edulis».
- `description`, `habitats` (где растёт), `harvests` (сбор/сушка) map cleanly.
- `is_toxic` / `toxicities` fit **very** well — poisonous-mushroom data is native
  to the existing toxicity fact table.
- The grounding guard (added 2026-06-03) keeps `medicinal_uses` empty on a
  non-medicinal guide instead of hallucinating them.

### What breaks or becomes wrong

1. **Kingdom overload.** Rows live in `plants` / `plants_v2`. A future agent
   query "покажи растения" would return mushrooms. Family facet mixes botanical
   families (Rosaceae) with fungal ones (Boletaceae).
2. **Part vocabulary mismatch.** `_PART_CANON` in `plant_extractor.py` is
   лист/корень/цвет/плод/кора/… Fungal parts are шляпка / ножка / мякоть /
   плодовое тело / гименофор — none canonicalised (they pass through as unknown).
3. **Matcher "гриб" magnet.** `plant_matching.py` tier-2 keys on stem nouns with
   `_MIN_KEY_TOKEN = 3`. The token «гриб» is shared across many names (белый
   гриб, польский гриб, …) — the exact failure mode we already fixed for «дерево»
   (demoted to a part-word). Generic recipe ingredient «грибы» would over-link to
   one species, and distinct mushrooms would collide.
4. **Edibility, not medicine, is the point.** A mushroom guide's core fact is
   `съедобно / условно-съедобно / несъедобно / ядовито` + culinary preparation.
   There is no structured slot for that today (see RFC-culinary-uses).
5. **Format blocker (#0).** The source is **.djvu**. The `extract` step expects
   PDF/OCR; DJVU almost certainly will not ingest as-is. Convert DJVU→PDF (or add
   a DJVU decode path to extract) before anything else.

## Goals

- Store fungi without polluting the "plants" namespace — a future agent can ask
  for plants, fungi, or both, unambiguously.
- Capture the fungi-native facts: **edibility class**, toxicity, look-alike
  warnings (ядовитые двойники), habitat, season, culinary preparation.
- Reuse as much of the existing pipeline (ingest → OCR → cleanup → translate →
  analyze → extract → index) as possible; do not fork a whole new stack unless
  justified.

## Options

### Option A — `kingdom` discriminator on the existing `Plant` table (recommended)

Add `Plant.kingdom` (`растение` | `гриб`, default `растение`). One table, one
collection, but every row is honestly tagged.

- Migration: additive column with default → backfill existing rows to `растение`.
- Extractor: set `kingdom='гриб'` for the fungi book (driven by the book's domain
  / a new `fungi` sub-mode, or detected from fungal family/latin).
- Matcher: add «гриб / грибы / гриба / грибной …» to `_PART_WORDS` (or a stop
  list) so it never acts as a tier-2 key — same treatment as «дерево».
- Parts: allow fungal parts to pass through (already do); optionally add a small
  fungal `_PART_CANON` extension (шляпка/ножка/мякоть/плодовое тело).
- Edibility: covered by RFC-culinary-uses (`plant_culinary_uses.edibility`), which
  is kingdom-agnostic and serves fungi directly.
- API/catalogue: add a `kingdom` facet; default plant views can filter
  `kingdom='растение'` so existing UX is unchanged.

Pros: minimal, additive, backwards-compatible; reuses the entire pipeline and the
cross-linking machinery. Cons: `plants` table is now a slight misnomer (a
"taxon" table); some plant-specific UI copy ("растение") needs neutralising.

### Option B — separate `mycology` domain + `fungi` table + `fungi_v2` collection

A parallel stack mirroring the plant stack.

Pros: cleanest separation; fungal schema can diverge freely (edibility class,
spore print, look-alikes as first-class). Cons: large — new model, new pipeline
branch in `workflows.py`, new extractor, new collection, new API + UI; duplicates
the cross-linking logic. Comparable in size to a separate product.

### Option C — don't ingest fungi yet

Defer. The mushroom guide stays out of the KB until A or B is funded.

## Recommendation

**Option A.** A `kingdom` tag plus the «гриб» stop-word and the culinary_uses
edibility field captures ~all the value (edible/toxic/where/season/how-to-cook,
cross-linking to recipes) at a fraction of Option B's cost, and keeps a single
searchable taxon space. Promote to Option B only if fungal-specific structure
(spore prints, microscopy, look-alike graphs) becomes a real requirement.

## Work items (Option A)

1. **Format:** convert «Большой определитель грибов — Юдин 2001.djvu» → PDF
   (blocker; do first).
2. **Matcher:** add «гриб» family of tokens to `_PART_WORDS` in
   `plant_matching.py`; verify no regression on existing plants. (cheap, ship
   independently — also helps any mushroom mentions in plant/recipe books.)
3. **Model:** `Plant.kingdom` column + Alembic revision + backfill `растение`.
4. **Extractor:** set `kingdom` for fungi; optional fungal part canon.
5. **Edibility:** land RFC-culinary-uses (`plant_culinary_uses`) — shared with
   plant cookbooks; add `съедобно/условно-съедобно/несъедобно/ядовито` controlled
   values and a "look-alike / двойники" caution field useful for fungi.
6. **API/UI:** `kingdom` facet; default plant catalogue filters to `растение`.

## Dependencies

- **RFC-culinary-uses** — edibility lives there; this RFC reuses it for fungi.
- Grounding guard (already shipped) — keeps a fungi guide from inventing medicine.

## Open questions

- Do we want `условно-съедобно` semantics richer than a single enum value (e.g.
  "after отваривание/вымачивание")? Could fold into `culinary_uses.caution`.
- Should `family`/`family_latin` be split per kingdom in the family facet, or is
  the `kingdom` tag enough to disambiguate? (Lean: tag is enough.)
