# RFC: Medical normalizer — controlled vocabularies for medicinal *actions* and *indications* (with an archaic→modern bridge)

Status: **Draft** · Created: 2026-06-04

## Summary

We can already answer "какие растения содержат сердечные гликозиды" (compound
vocabulary, reference-normalizer instance #1). We **cannot** cleanly answer the
medical counterpart — "какие растения от кашля / при водянке" — even though the
medical fact layer is the second-largest in the corpus:

| | count | form |
|---|---|---|
| `plant_medicinal_uses` | **6936** facts | — |
| with `indications` (показания) | 6012 | **free text, no FK at all** |
| with `action_raw` | 6817 | free text; **2034 distinct** |
| linked to `action_id` | 2833 (41%) | `MedicinalAction` = **42 hand-seeded terms** |

A load-test query — *"какой сбор от кашля я могу сделать из того, что растёт
неподалёку, с учётом сезона"* — exposed the gap precisely: filtering by the free
`indications` field yields **9** plants, filtering by the (un-normalized) *action*
axis yields **138**. The recall lives in the action axis, the user speaks in the
indication axis, and neither is reconciled.

This RFC applies the proven **reference-normalizer** mechanism (controlled vocab +
idempotent corpus normalize) to the medical layer, on **two** axes, plus the
feature that makes a historical corpus uniquely valuable: an **archaic→modern
bridge** (водянка→отёки/асцит, грудная жаба→стенокардия, золотуха→скрофулёз,
падучая→эпилепсия, антонов огонь→гангрена).

## What's different from the compound normalizer (important)

The compound vocab is **built from one property-first book** (Георгиевский) via an
`extract_vocabulary` pipeline step that reads the book's prose. The medical case
is **not** book-shaped that way:

- Phytotherapy books — old and modern — are **plant-organized** (entity-first).
  Loaded as `herbalism`, they **already** fill `PlantMedicinalUse.action_raw` and
  `.indications`. They are the *source of raw data*, not a property-first reference.
- Therefore the medical vocabulary must be **corpus-bootstrapped**: canonicalize
  the ~2034 distinct `action_raw` strings and the distinct `indications` atoms the
  corpus has already accumulated, rather than parse a single dictionary book.

Consequence: the medical normalizer is a **corpus-wide maintenance operation**
(like `POST /api/plants/dedupe-latin` and `/relink-recipes`), **not** a per-book
pipeline step. New phytotherapy books keep flowing through the unchanged
`herbalism` pipeline; the normalizer is re-run afterwards to fold their new raw
terms into the vocab and relink the whole corpus.

### Grounding note

The grounding guard that protects *extraction* does **not** apply to vocabulary
*canonicalization*. The facts (plant X used for водянка) were already grounded
verbatim when the herbalism extractor wrote the `PlantMedicinalUse` row. Building
the mapping "водянка → отёки/асцит" is legitimate reference knowledge — that is
the whole point of the bridge — so the vocab builder may use the model's medical
knowledge. It only ever maps strings the corpus already contains; it never invents
new plant↔use facts.

## Data model

Mirror `Compound` / `MedicinalAction`. Migration `009_medical_normalizer`.

### 1. Grow `MedicinalAction` (actions axis)

It exists but is thin (`id, parent_id, name, name_modern, system`). Add to match
the matcher's needs and the bridge:

- `synonyms: ARRAY(String)` — alternate spellings to match `action_raw` against.
- `source_book_id: UUID|None` — provenance (nullable; corpus-bootstrapped terms
  carry NULL).

Canonical `name` stays the historical/common Russian action term ("отхаркивающее");
`name_modern` carries the clinical synonym ("экспекторантное"). Two-level hierarchy
via `parent_id` (group "действие на дыхательную систему" → "отхаркивающее").

### 2. New `Indication` vocab (indications axis)

```python
class Indication(Base):
    __tablename__ = "indications"
    id, parent_id            # hierarchy: "болезни органов дыхания" → "кашель"
    name        Text unique  # canonical term — MODERN where a modern term exists
    name_modern Text | None  # explicit modern/clinical name when `name` is kept historical
    synonyms    ARRAY(str)   # alternate spellings/forms (genitive, OCR variants)
    archaic     ARRAY(str)   # *** the bridge: pre-modern names mapped to this concept
    system      String(50)   # body system: дыхание/ЖКТ/ССС/ЦНС/кожа/мочеполовая/...
    definition  Text | None  # short gloss, optional
    source_book_id UUID|None
```

The **`archaic` array is the headline feature**: "водянка" lives in
`archaic` of the concept whose `name`="отёки". A query for either the archaic or
the modern term resolves to the same concept, so a 19th-c. лечебник and a modern
user meet in the middle.

### 3. Link `PlantMedicinalUse` → indications

`indications` is free text and often holds **several** indications
("лихорадка, кашель"). Rather than a join table, add an array of vocab ids
(consistent with the codebase's `toxic_parts` / `edible_parts` / `synonyms`
arrays; queried with `&&` over a GIN index):

- `indication_ids: ARRAY(UUID)` on `PlantMedicinalUse`.

The normalize pass splits `indications` into atoms (on `,` / `;` / `и`), maps each
atom to an `Indication`, and stores the resolved set. (`action_id` stays a single
FK — one action per use row — exactly as today.)

> Trade-off: an `ARRAY(UUID)` is not a real FK, so a deleted vocab row could leave
> a dangling id. Acceptable: vocab rows are upserted/grown, never deleted in normal
> operation, and the normalize pass is a full recompute that would drop stale ids
> on the next run anyway. Chosen for simplicity + codebase consistency over a
> 3rd join table.

## Mechanism

Two corpus-wide phases, both idempotent, both re-runnable (a new book just means
re-run). Exposed under a new `/api/medical` router and a single corpus op.

### Phase A — build/grow the vocabularies (`build_medical_vocab`)

A new service `medical_vocab.py`:

1. Pull `SELECT DISTINCT action_raw` (~2034) and the split atoms of
   `SELECT DISTINCT indications` from `plant_medicinal_uses`.
2. Batch (~150 terms/call) to the LLM with a **canonicalization** prompt (not an
   extraction prompt). For each raw term return: canonical `name`, `name_modern`,
   `parent`/group, `system`, `synonyms`, and — for indications — `archaic` (is
   this raw term itself an archaic name? what modern concept does it denote?).
3. Upsert into `MedicinalAction` / `Indication` with the cumulative,
   never-clobber `_upsert_*` discipline already used for compounds (fill empty
   fields, union arrays, set parent once).

This is the part that differs from `compound_extractor` (which reads book prose):
input is the **distinct raw strings**, output is their canonical taxonomy. No
grounding; the strings are already corpus-resident.

### Phase B — normalize the corpus (`normalize_medical_uses`)

A new service `medical_matching.py`, the medical analog of
`compound_matching.normalize_plant_compounds`:

1. Build exact (name/name_modern/synonyms/archaic → id) and token-subset indices
   for both vocabularies, with the same conservatism — most-specific token-subset
   wins, ambiguous ties → leave NULL.
2. `UPDATE plant_medicinal_uses SET action_id=NULL, indication_ids='{}'` then
   re-derive both from `action_raw` and the split `indications`. Idempotent full
   recompute.
3. Report `{actions_linked, indications_linked, uses_total, action_vocab,
   indication_vocab}`.

Note: this finally relinks **actions corpus-wide**. Today `action_id` is only set
at *extraction* time against the 42 seeds (activities.py:1090) and never relinked
when the vocab grows — Phase B fixes that the way compounds already work.

## Query surface (what this unlocks)

- `plants.py` already filters by `action` against both `action_id` and
  `action_raw` (lines 184-191) — extend it to also expand to the action's
  hierarchy descendants and to filter by **indication** (concept + descendants +
  archaic).
- New `/api/medical`: `GET /actions`, `GET /indications`, `GET
  /indications/{id}` (concept + archaic names + plants that treat it),
  `POST /normalize` (run Phase B), `POST /build-vocab` (run Phase A).
- New MCP tool `plants_for_condition(symptom_or_action, kingdom?, …)` — resolve a
  user symptom ("кашель", or archaic "водянка") → concept → its action cluster →
  plants, source-grounded. This is the medical sibling of the existing search
  tools and the first half of the eventual `remedy_for(symptom, region, date)`
  composite (the geo + season legs already exist via `find_observations`).

## Out of scope (future)

- A harvest-season parser (`plant_harvests.season` free text → month range) — the
  remaining leg of the "сбор сейчас" query; separate small RFC.
- An indication-first *reference* book (Спр. по фитотерапии organized by disease)
  could additionally seed the vocab via an `extract_vocabulary`-style step; not
  needed for v1 since the corpus already carries the raw data.
- A `remedy_for(symptom, region, date)` composite MCP tool stitching medical +
  iNat geo + season.

## Validation plan

1. Run Phase A on the current corpus; eyeball the vocab (esp. archaic mappings).
2. Run Phase B; expect action-link coverage to jump well past today's 41% and a
   non-trivial indication-link coverage on the 6012 indication-bearing uses.
3. Re-run the cough load-test by **indication** ("кашель") and confirm recall now
   approaches the action-axis 138, and that an archaic query ("водянка") resolves.
