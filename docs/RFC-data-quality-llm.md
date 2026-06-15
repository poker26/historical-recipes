# RFC: LLM-adjudication layer for the data-quality linter

Status: **Draft / building** · Created: 2026-06-10 · Builds on `RFC-data-quality.md`

## Problem

The linter finds *candidates* cheaply and deterministically — but at corpus scale
the open findings number in the **thousands** (alias.collision alone = 1305 P0;
mixed_script 550; etc.). Manual triage in `/quality` caps out at hundreds. Several
classes are inherently **fuzzy** — they need judgment, not a rule:

- `alias.collision` — is this shared name a real matcher landmine (strip it) or a
  legitimate folk name two plants genuinely share (keep)?
- `identity.name_vs_latin` (the «жидовская вишня» class) — does our RU name
  semantically match the binomial's accepted vernaculars?
- "is this even a recipe?" (the «Ампевит» description-as-recipe case), "is this a
  plant vs a substance/animal?", "are these two cards the same species (merge)?",
  grounding ("is the extracted fact actually in the source text?").

Deterministic bulk-fixes already cleaned the *clean* classes (kingdom via GBIF,
trap-cards via head-noun, monograph-recipes). What's left is the judgment pile.

## Architecture: three layers, not two

```
Linter (deterministic)  →  LLM adjudicator      →  Human
finds CANDIDATES            renders a VERDICT       spot-check +
(thousands, cheap)          {real|false|uncertain}  bulk-approve
                            + action + reasoning    (dozens, not thousands)
```

The LLM is a **judgment layer**, not a fixer: it reads a finding + its grounded
context and returns a structured verdict. High-confidence verdicts flow to a
bulk-apply queue; uncertain ones surface to the human in `/quality`. The human
reviews ~tens of disputed cases, not thousands.

## Verdict model

Each finding gains LLM fields (migration, additive nullable):
`llm_verdict` (real | false_positive | uncertain), `llm_confidence` (0–1),
`llm_action` (the suggested concrete fix, e.g. strip_alias / delete / merge / keep),
`llm_reasoning` (short, MUST cite the finding's data), `llm_model`, `llm_at`.

Status flow: `open` → (adjudicator) the finding keeps `open` but gains the verdict
→ human (or a confidence-gated auto-apply) moves it to confirmed/dismissed/fixed.
Sticky as before — re-adjudicating a human-resolved finding is skipped.

## Adjudicator

Per `check_id`: a **context builder** (pulls the finding's entities + the minimal
facts the LLM needs — e.g. for alias.collision: plant A name+latin, the alias,
plant B name+latin) and a **prompt template** asking for the structured verdict.
One registered adjudicator per check, mirroring the validator registry.

- **Grounding is mandatory.** The verdict must reference concrete data (names,
  latin, source text) — never "I think". Otherwise we manufacture hallucinations
  *during* cleanup. The prompt forces a citation; ungrounded verdicts → uncertain.
- **Cheap model for triage.** real/false is an easy call → `LLM_MODEL_LIGHTWEIGHT`
  (qwen3-32b), not the 235b. Escalate to a bigger model only for genuinely hard
  classes (semantic name↔latin).
- **Cache.** A verdict on a finding is cached (the row itself) — re-runs skip
  already-adjudicated findings, like the GBIF/iNat caches. BYOK Qwen quota makes
  the thousands of calls feasible (no 429 wall), but we don't re-spend tokens.

## Infrastructure

`DataQualityAdjudicateWorkflow` — durable Temporal sweep on the **dispatcher
queue** (lightweight orchestration, isolated from the pipeline worker, same lesson
as BookDispatcher). Batches open findings of a check through an LLM activity until
none remain; resumable; paced. Plus a synchronous `POST /api/quality/adjudicate`
for small/interactive runs. `/quality` UI gains an `llm_verdict` column + filter
and a "bulk-apply high-confidence" action.

## Auto-apply policy

- `llm_verdict=false_positive`, conf ≥ 0.9 → auto-`dismissed` (it was noise).
- `llm_verdict=real`, conf ≥ 0.9, action is a known **auto_fixable** op (strip_alias,
  delete_recipe, …) → eligible for **bulk-apply** (one human click per check, not
  per finding).
- conf < 0.9 OR a destructive non-reversible action → **human** in `/quality`.
- Identity/merge actions stay human-gated regardless (a wrong merge is worse than a
  flagged stub — same philosophy as the deduper).

## Build order

1. Migration: `llm_*` columns on `data_quality_findings`.
2. Adjudicator framework (registry + context builder + structured-LLM call) and the
   FIRST adjudicator: `alias.collision` (highest volume, clear strip/keep decision).
3. `POST /api/quality/adjudicate?check_id=&limit=` (synchronous, paced) — prove the
   loop on a small batch.
4. `DataQualityAdjudicateWorkflow` (Temporal, dispatcher queue) for full runs.
5. `/quality` UI: verdict column + filter + bulk-apply.
6. Add adjudicators per fuzzy class (name_vs_latin, is-recipe, is-plant, merge).

## Relation
Sits on top of `RFC-data-quality.md` (the deterministic linter). The validators
produce candidates; this RFC adjudicates them. The matcher root-cause fix
(don't bind on a bare head-noun) is parallel and reduces future candidate volume.

## Two LLM layers — alignment with `RFC-reader-monograph.md` (2026-06-10)

There are **two distinct LLM layers**, and they must not be conflated:

- **Layer 1 — clean the DATA (this RFC).** LLM adjudicates findings → fixes the
  source-grounded facts in the DB (strip bad aliases, delete junk cards, merge
  canonical dups, resolve toxic/edible contradictions). Output: clean facts.
  Consumers: agents (MCP raw) **and** Layer 2.
- **Layer 2 — generate reader TEXT (`RFC-reader-monograph.md`).** LLM polishes the
  (cleaned) facts into a capped, human-readable «reader monograph» stored in DB and
  served by `?view=field`. Consumer: humans.

**Ordering: Layer 1 first.** Generating a monograph from dirty data = polishing
garbage. The two share one skeleton — LLM-batch over entities + mandatory grounding
+ hash/verdict cache + review-gate + Temporal on the dispatcher queue — so we build
it once and generalise (findings for L1, plants for L2).

Layer 2's publish-gate checks (`ungrounded_claim` P0, `identity_conflict` P0 — the
Хмель toxic+edible case, `residual_ocr`/`dup_not_merged`/`stub_leak` P1) are NEW
validators added to *this* linter's registry. The action/compound **synonym
canonicalisation** is best done in Layer 1 (a cleanup class — benefits agents too),
not only at L2 generation time; L2 keeps OCR/prose/verdict/cap (inherently
presentation). `identity_conflict` (toxic+edible) is worth adding to L1 early — it
serves both raw data and the monograph.
