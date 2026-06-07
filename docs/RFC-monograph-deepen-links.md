# RFC: make monograph links *deepen the plant*, not pivot to a plant list

Status: **Proposed** · Author: «Что растёт» Android client agent · Created: 2026-06-08

> Companion to `HANDOFF-monograph-aggregation.md` (the `?view=field` contract,
> live) and `RFC-field-view-data-noise.md` (QA fixes, done). This RFC is a
> **product** change to what a tap *does* in the field monograph, plus the
> reference enrichment that makes it possible. Priorities below are the product
> owner's explicit decisions (2026-06-08).

## Problem — the links answer the wrong question

After a forager photographs a plant, identifies it, and opens the monograph, the
tappable links in **«Главное»** (medicinal actions) and **«Химический состав»**
(compounds) currently navigate to *a list of other plants that share that action /
that compound*. Standing over the plant in the forest, «глюкоза есть ещё у 20
растений» helps nothing. Only **«Рецепты»** is organised right: tap → read the
recipe.

**Principle:** in the monograph, a tap should **deepen understanding of THIS
plant**, not jump sideways to a list. The "other plants with X" view is a
*discovery / browse* mode (find a plant for a condition) — useful at home, wrong as
the default tap in the field. We **demote** it, not delete it.

## What data we actually have (verified 2026-06-08 against prod)

| Thing | State today |
|---|---|
| **Action definitions** (вяжущее, мочегонное…) | **Nowhere.** `list_vocabulary("actions")` returns only `{value, count}` — bare strings from `medicinal_uses.action`, no `id`, no `definition`. |
| **Indication definitions** | Curated indication vocab **has** `definition` (e.g. «вяжущее — сужение пор, ↓секреции за счёт танинов»), coverage uneven. |
| **Compound definitions** | `get_compound.definition` **exists**, coverage incomplete and some are *wrong* (e.g. «дубильные вещества» → «вещества в соцветиях бессмертника» — a stray plant-specific scrap, not what tannins are). |
| **Per-use source quote** | `medicinal_uses.original_text` **present & populated** (verbatim quote + `source` book). e.g. Герань: «…для компрессов: 2 ст. ложки варить 10 минут в литре воды». |
| **Compound source quote** | None — only `notes` (often a %) + `source`. |
| **«Интересный факт»** | No field. |

## Product decisions (priorities)

1. **Action definitions = lead priority.** (Doubles / «как отличить» deferred —
   needs separate research.)
2. **«Интересный факт» — yes, build it.**
3. **Source quote: show ONE** representative quote, with a **«читать ещё»** link to
   the rest.

---

## Part A — reference enrichment (backend data, «всё в наших руках»)

### A1 (LEAD). Build an **actions vocabulary** with definitions

Actions are not a concept vocabulary today — they're raw strings. Create one
mirroring the indication-concept shape:

- a controlled `action` concept: `id`, `name`, `name_modern?`, `synonyms?`,
  **`definition`** (1–2 forager-facing sentences: *what the action is + what it
  does in the body*), `linked_facts`.
- normalize the distinct `medicinal_uses.action` strings onto these concepts
  (the set is bounded — a few hundred raw → ~80–150 canonical; pure pharmacology,
  low hallucination risk, but **review-gated** per the project's extraction
  history).
- definition style example: *«Вяжущее — стягивает ткани и слизистые, уменьшает
  выделения и кровоточивость (за счёт дубильных веществ).»* Plain, no jargon,
  explains *why the plant works*.

This single deliverable lights up the «Главное» tap for every plant at once.

### A2 (secondary, same mechanism). Backfill + **clean** compound definitions

Fill `compound.definition` gaps **and rewrite the wrong ones** (the tannin example
above). Same forager-facing «что это + что делает» style. Powers the «Химический
состав» tap.

### A3. Author a per-plant **«Интересное»** fact — **grounded + sourced**

New nullable per-plant field `fun_fact: { text, source }`. One or two sentences:
etymology, historical/ethnobotanical use, a striking property. **Hard constraint
given the project's hallucination history:** it must be **derived from the corpus
or a citable reference and carry a `source`** — never freely generated. Start with
plants whose `description` already contains such material.

---

## Part B — `?view=field` contract additions (embed, no field round-trips)

Definitions and one quote are small; embed them inline so a tap reveals content the
client already holds (consistent with the "no load-more in the field" design). The
only lazy path is the opt-in «читать ещё».

```jsonc
// uses[] — gains the action's meaning + one grounded quote
{
  "action": "вяжущее",
  "action_id": "<uuid|null>",          // tappable action concept (A1)
  "action_definition": "Стягивает ткани…",   // null until A1 lands
  "parts": ["трава"],
  "indications": ["раны", "поносы"],
  "indication_ids": ["<uuid>"],
  "source_count": 7,                    // already present → label «читать ещё (6)»
  "quote": {                            // ONE representative original_text (decision #3)
    "text": "Герань кроваво-красная — для компрессов: 2 ст. ложки варить 10 мин в литре воды.",
    "source": "Носаль 1960"
  }
}

// compound_groups[].examples[] — gains a short substance definition (A2)
{ "name": "дубильные вещества", "compound_id": "<uuid|null>",
  "definition": "Вяжущие полифенолы (танины); осаждают белки — отсюда кровоостанавливающее и противовоспалительное действие." }

// top-level — «Интересное» (A3)
"fun_fact": { "text": "…", "source": "…" }   // or null
```

- **«читать ещё (N)»**: when `source_count > 1`, the client lazily fetches the
  remaining `original_text` rows for that action. Reuse the **default**
  `GET /plants/{id}` (it already returns every `medicinal_uses.original_text`) —
  no new endpoint needed; the client filters by action. Primary content (1 quote +
  definition) stays inline so the forest case needs no network.
- **`quote` selection** (server): from the rows backing the action, prefer one that
  carries a concrete preparation/dosage (actionable) and a clear citation; trim to
  ~240 chars on a word boundary.
- All new fields **nullable/optional** — client omits a block when absent, so this
  ships incrementally (A1 first → action_definition populates; A2 → compound defs;
  A3 → fun_fact).

## Part C — client (my side, after the contract lands)

Thin, no logic: re-point taps to a **definition sheet**, not the plant list.

- **action tap** → sheet: `action_definition` + the one `quote` (text + source) +
  «читать ещё (N)» (lazy) + a small, secondary «🔎 Ещё растения с этим действием»
  (the existing ConceptDetail, demoted).
- **compound tap** → sheet: `definition` + secondary «🔎 Ещё растения с этим
  веществом».
- add an **«Интересное»** line near the top from `fun_fact`.

## Scope / non-goals

- **Doubles / «как отличить»** — explicitly deferred (research needed). Not here.
- The plant-list cross-reference is **demoted, not removed** — discovery still
  works from the sheet's secondary link; no payload change for it (still lazy).
- Phase 1 stays **deterministic for aggregation**; the *new* authored text
  (definitions, fun_fact) is **review-gated and sourced**, not request-time LLM.
- Overlaps `RFC-data-quality.md`: the bad-definition class (A2) is the same
  «линтер гербария» family — a `compound.definition_implausible` check could seed
  the cleanup.
