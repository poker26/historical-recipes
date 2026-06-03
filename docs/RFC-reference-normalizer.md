# RFC: Reference-normalizer pipeline (first instance: phytochemistry / compound vocabulary)

Status: **Draft** · Author: pipeline notes · Created: 2026-06-03

## Summary

We have ingested two *shapes* of source so far, each an **entity-first** book:

- a plant determiner / herbal → one `Plant` per entry (`herbalism` domain);
- a recipe / cookbook → one `Recipe` per entry (`recipes` domain).

A third shape exists and we have no pipeline for it: a **reference organized by a
property or relation, not by an entity** — e.g. «Биологически активные вещества
лекарственных растений» (Георгиевский, Комиссаренко, Дмитрук), organized by
*compound class* (Алкалоиды / Сердечные гликозиды / Флавоноиды / Сапонины /
Кумарины / Дубильные вещества / Эфирные масла / Витамины …), each chapter listing
which plants contain the class and what it does pharmacologically.

Forcing such a book through entity-first extraction throws away its systematic
value (the chemistry taxonomy) and keeps only scattered plant↔compound mentions.

This RFC proposes a **new pipeline shape — the reference-normalizer** — whose
product is NOT entities but:

1. a **controlled vocabulary** (a hierarchical taxonomy + synonyms), and
2. a **corpus-wide normalization pass** that backfills existing free-text fields
   to the new vocabulary IDs (idempotent, re-runnable — the analog of
   `relink_recipe_ingredients`).

Its first concrete instance is a **compound vocabulary** that also fixes a
standing gap: `PlantCompound.compound` / `compound_group` are free text today, so
"какие растения содержат сердечные гликозиды?" is unanswerable across the corpus.

## Motivation: two problems, one mechanism

- **New source shape.** Property-first references don't fit entity extraction.
- **Un-normalized fields.** In `backend/app/models/plant.py`, only
  `MedicinalAction` is a controlled, hierarchical vocabulary (`action_id` on
  `PlantMedicinalUse` next to the raw `action_raw`). `PlantCompound` has no such
  backbone — `compound` / `compound_group` are bare strings, never reconciled
  across books, so they cannot be faceted or queried.

Both are solved by a pipeline that **produces a vocabulary and normalizes the
corpus against it.** `MedicinalAction` already proves the read-side value of a
controlled vocab; today it is hand-seeded. This RFC makes *building and growing*
such vocabularies a first-class, repeatable pipeline.

## The reference-normalizer pipeline (new domain)

A new domain / source-type whose primary output is a vocabulary + a global
normalize, parametric over the **target field** it normalizes (compound, action,
…). It reuses the shared front of the pipeline and diverges at structuring:

```
reference:  classify → extract → cleanup → translate → analyze
                     → extract_vocabulary
                     → normalize_corpus
                     → index
```

(compare in `backend/app/temporal/workflows.py`:
`PIPELINE_STEPS_RECIPES` / `PIPELINE_STEPS_HERBALISM`.)

- **extract_vocabulary** — read the reference as *term monographs*: for each term
  emit `{name, parent, synonyms[], class/kind, definition, original_text}` plus
  any entity↔term assertions it states (e.g. "растение X содержит вещество Y в
  части Z"). Section/size chunking — there are no per-entity headers, so
  `_HEADER_RE` does not apply.
- **normalize_corpus** — a deterministic + fuzzy pass that maps existing
  free-text fields across the WHOLE database to vocabulary IDs. Idempotent global
  recompute, exactly like `relink_recipe_ingredients` in
  `backend/app/services/plant_matching.py`: safe to re-run whenever the vocab
  grows. Exposed as a manual endpoint too.

Key property: a reference book's effect is **corpus-wide, not book-local.**
Ingesting the phytochemistry book retroactively upgrades the compound facts of
*every herbal already loaded*.

## First instance: compound vocabulary (phytochemistry)

### Model
- `compound_groups` / `compounds` — one table with `parent_id` (mirror
  `MedicinalAction`): `id, parent_id, name, name_latin, synonyms[] (ARRAY),
  compound_class, definition, source_book_id`. Hierarchy:
  `фенольные соединения → флавоноиды → рутин`; `гликозиды → сердечные гликозиды
  → дигитоксин`.
- `PlantCompound.compound_id` FK → vocabulary (alongside the existing raw
  `compound`/`compound_group`, exactly like `action_raw` + `action_id`).
- Optional `compound_actions` bridge (`compound_id` ↔ `MedicinalAction.id`):
  encode "сердечные гликозиды → кардиотоническое", linking chemistry to use.

### Extraction
- A compound-centric extractor + prompt: read the book as compound monographs,
  not plant monographs. Emit the term, its parent class, synonyms, the plants it
  occurs in (+ part), pharmacological action, and a verbatim `original_text`.
- Cross-link plant assertions to existing `Plant` rows via the matcher; create a
  stub plant only when absent (same policy as recipe ingredient linking).

### Normalization mechanics
- Reuse the matcher discipline (normalized exact → synonym table → stem-token),
  add **embedding similarity** because OCR mangles chemical names.
- **Ambiguity guard** (same philosophy as the plant deduper): a wrong normalize
  is worse than an unnormalized stub — if two vocab terms match a string
  equally, leave it raw rather than guess.

## Grounding

The grounding guard (shipped 2026-06-03 for plant facts) is **most critical
here**: the model knows phytochemistry cold and will happily recite which
alkaloids are in belladonna from memory. Every vocabulary entry, definition, and
plant↔compound assertion must carry an `original_text` verbatim-traceable to the
source chunk; ungrounded output is dropped. We extract the *book's* taxonomy, not
the model's.

## Caveats

1. **Format — .djvu** (blocker #0): convert to PDF before ingest. Both
   phytochemistry and fungi sources arrived as DJVU.
2. **OCR of chemistry is lossy.** Structural formulas, sub/superscripts and
   tables will not survive as data — expect text + lists, with mangled names.
   This is exactly why synonym + fuzzy/embedding matching matters.
3. **Do not clobber hand-curation.** Vocabulary build must be additive + dedup;
   never overwrite existing `MedicinalAction` curation when wiring the
   compound→action bridge.

## Options

- **A (recommended):** build the general **reference-normalizer domain**
  (`extract_vocabulary` + `normalize_corpus`), with compounds as instance #1.
  More upfront work, but we already foresee further vocabularies (measurement
  units, botanical families, the medicinal-action vocab itself growing from a
  pharmacology reference).
- **B (lighter):** no new domain — a one-off extraction script seeds a compound
  table, and `relink`/normalize is extended to also map compounds. Cheaper, less
  general; pays the cost again for the next reference book.

Recommendation: **A.** The pattern recurs; making it a pipeline shape (not a
per-book script) is the leverage. `MedicinalAction` is instance #0 in spirit —
this generalizes how such vocabularies get built and grown.

## Work items (Option A)

1. **Format:** DJVU → PDF for the Георгиевский book.
2. **Model:** compound vocabulary table + `PlantCompound.compound_id` + Alembic
   (additive, backfill nothing — `compound_id` nullable).
3. **Domain wiring:** `PIPELINE_STEPS_REFERENCE` in `workflows.py`
   (`… analyze → extract_vocabulary → normalize_corpus → index`); `steps_for_domain`
   / `step_names_for_domain` extended.
4. **extract_vocabulary** activity + compound-centric extractor + grounding.
5. **normalize_corpus** activity — idempotent global recompute mapping
   `PlantCompound.compound` strings → `compound_id`; manual endpoint mirroring
   `POST /api/plants/relink-recipes`.
6. **(optional) compound_actions** bridge + extractor support.
7. **API/UI:** facet "плоды содержат вещество / класс веществ"; query
   "растения с веществом группы X".

## Relation to the other RFCs

- **RFC-culinary-uses** and **RFC-fungi** change the ENTITY shape (new fact
  table / a kingdom tag). **This** RFC adds a new PIPELINE shape. Orthogonal;
  they share only the grounding guard and the relink/normalize discipline.
- The fungi RFC's edibility facet and this RFC's compound facet are both "make a
  free-text property queryable" — the normalize_corpus mechanism here is the
  generic tool that future facets can reuse.

## Open questions

- **One generic vocab table or per-domain tables?** `MedicinalAction` is its own
  table; compounds likely deserve their own (type-specific columns: `name_latin`,
  `compound_class`). A single generic `(term, parent, synonyms, kind)` table
  would unify the pipeline but lose typed columns. Lean: **per-domain tables,
  shared pipeline.**
- **Auto vs manual normalize.** Run `normalize_corpus` automatically at the end
  of a reference ingest AND expose a manual trigger (like relink). Lean: **both.**
- **Stub plants from a chemistry book** — acceptable, or hold compound assertions
  until the plant exists from a determiner? Lean: create stubs (cross-linking
  value), same as recipe ingredients.
