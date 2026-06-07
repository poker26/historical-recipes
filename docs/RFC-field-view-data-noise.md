# RFC: data noise leaking into the field monograph (`?view=field`)

Status: **Proposed** · Author: «Что растёт» Android client agent · Created: 2026-06-07

> Companion to `HANDOFF-monograph-aggregation.md` (the `?view=field` contract).
> That handoff is **implemented and live** — the shape is correct and the client
> renders it. This RFC is the QA follow-up: three classes of *content* noise that
> the deterministic aggregation faithfully passes through to the phone, where a
> forager sees raw classifier codes, redundant labels, and un-split action blobs.
> All three are **server-side** (extraction + aggregation). The client stays thin
> by agreement — we are deliberately **not** masking these on the device.

## Why this matters

This is a field app on a weak connection. The monograph is the payoff after a
photo identifies a plant. When «Главное» / «Химический состав» show machine
artefacts, the screen reads as broken and the credibility of the whole corpus
drops. None of these are layout problems — the client prints exactly what the API
sends. The fixes belong in the data and the aggregator.

All examples below are from the **live** `?view=field` responses, verified
2026-06-07 against prod (`46.173.19.68:8126`).

---

## Issue 1 — ICD-10 codes leak into `uses[].indications`, then get fragmented

**Plants:** Купена лекарственная (`0e68696f-…`), Вяжечка голая / *Turritis glabra*
(`52ab5b25-…`), and presumably any plant extracted from the same book.

**What the API returns** (Вяжечка, one `uses` row):

```json
"indications": [
  "К26",
  "К28)",
  "воспалительные заболевания кишечника (К50-К52)",
  "дуоденит (К29.8)",
  "хронический гастрит (К29.3-К29.5)",
  "язвенная болезнь (К25"
]
```

Купена shows the same with respiratory codes: `"J80-J99)"`,
`"болезни органов дыхания (J00-J47"`, `"отёки (R60) …"`.

**Two defects stacked:**

1. **Codes were written into the indication text at extraction time.** The LLM
   extractor appended ICD-10 classifiers to each indication, e.g. the source
   concept is really *«язвенная болезнь желудка (K25, K26, K28)»*. These codes are
   meaningless to the end user and were never asked for. **Note they use Cyrillic
   `К`, not Latin `K`** (`К26`, `К29.8`) — so any downstream code-stripping regex
   must match the Cyrillic homoglyph, and a client-side Latin-only filter would
   silently miss them (another reason to fix server-side).

2. **The aggregator split inside the parentheses.** Splitting the original string
   on commas tore `«… (K25, K26, K28)»` into `"язвенная болезнь (К25"`, `"К26"`,
   `"К28)"` — a half-open paren, then two bare codes. So even a clean concept comes
   out as garbage fragments.

**Proposed fix (do both):**

- **Extraction (root cause):** stop writing ICD/MKB codes into indication free
  text. If a classifier code is wanted at all, it belongs in a separate structured
  field (`icd10`), never concatenated into the human label. A grounding/cleanup
  pass over already-ingested `medicinal_uses.indication_raw` can strip a trailing
  parenthetical that is *only* codes: regex `[A-ZА-Я]\d{2}(\.\d+)?(\s*[–-]\s*[A-ZА-Я]?\d{2}(\.\d+)?)?`
  (note Latin **and** Cyrillic letter class), then drop an emptied `(...)`.
- **Aggregation (defence in depth):** when building `indications`, split on a
  separator that is **paren-aware** (don't split inside `(...)`), and drop any
  resulting token that is *only* a code / punctuation after the strip above.

**Acceptance:** for both plants above, `indications` contains only readable
Russian terms — no bare `Кxx`/`Jxx`, no half-open parens, no `")"`-suffixed scraps.

---

## Issue 2 — redundant `compound_groups`: group label == its single example, with a `count: 1`

**Plant:** Вяжечка голая (`52ab5b25-…`), «Химический состав».

**What the API returns:**

```json
{ "group": "гликозиды",   "count": 1, "examples": [{ "name": "гликозиды" }] }
{ "group": "флавоноиды",  "count": 1, "examples": [{ "name": "флавоноиды" }] }
{ "group": "клетчатка",   "count": 1, "examples": [{ "name": "клетчатка" }] }
{ "group": "пурины",      "count": 1, "examples": [{ "name": "пурины" }] }
{ "group": "эфирное масло","count": 1, "examples": [{ "name": "эфирное масло" }] }
```

The client renders each as **«Флавоноиды · 1 · флавоноиды»** — the group name, a
useless `1`, and the same word again. The product owner's words: *«Если компонента
одна, надо только её и указывать, без цифр и без повторов.»*

**Root cause:** the source only stated the group (*«содержит флавоноиды»*) with no
specific named compound. The aggregator created a group whose lone example is a
copy of the group label, and emitted `count: 1`.

**Proposed fix (aggregation):**

- **Dedup example name against the group label** (trimmed, case-insensitive). If
  the only example *is* the group, drop the example list — the group label alone is
  the content. (For Вяжечка: `углеводы → сахара`, `минералы → минеральные соли…`
  are *not* redundant and must be kept — the example adds information; only collapse
  when example == group.)
- Treat `count` as a **display hint, not a fact to print**: the client should show
  it only as «ещё N» when `count > len(examples)`. But since some renderers (ours)
  surface it directly, please **omit `count` when it is `1`** (or when
  `count == len(examples)` and there's nothing hidden) so a 1-compound group can
  never render a stray number.

**Acceptance:** a group with one compound equal to its name renders as a single
label («Флавоноиды»), no digit, no repeat. A group with a distinct example still
shows «Минералы: минеральные соли (калий, кальций, магний)».

---

## Issue 3 — `uses[].action` is a multi-action blob, not one ranked action (bonus, same root)

Spotted while verifying the above; not yet reported by the product owner but it
defeats the whole point of the ranked «Главное» list.

**What the API returns** (Вяжечка, the single `uses` row):

```json
"action": "диуретический, анальгетический, противовоспалительный"
```

Купена is worse — one `action` string holds **seven** comma-joined actions.

**Why it's wrong:** the «Главное» contract (`HANDOFF-monograph-aggregation.md`)
ranks **distinct actions** by `source_count`. Here three (or seven) separate
actions are crammed into one dedup key, so: (a) they can't be ranked or deduped
against the same action from another book, and (b) the client prints a run-on line
instead of «противовоспалительное», «мочегонное», … as separate tappable items.

**Proposed fix (extraction or aggregation):** the action field should hold **one**
action. Split `action_raw` on commas (and «и») into separate action facts *before*
the dedup/rank step, so each becomes its own `uses` row and merges with the same
action from other sources. If the controlled-vocab `action.name` is already
single-valued, prefer that as the dedup key and only fall back to splitting the
raw string.

**Acceptance:** Вяжечка's «Главное» lists «мочегонное», «обезболивающее»,
«противовоспалительное» as three rows; Купена's seven actions are seven rows,
each with its own `source_count`.

---

## Scope / non-goals

- **No client changes.** By the thin-client agreement the phone renders the
  contract verbatim; masking noise on-device would hide a corpus-quality signal and
  duplicate logic across consumers (MCP `get_plant` has the same raw data).
- These overlap with the existing **`RFC-data-quality.md`** «линтер гербария»
  effort (name↔latin↔kingdom, alias mines, OCR-latin). Issues 1–3 are the same
  *class* — extraction artefacts surfacing to users — and could be folded in as
  validators: `indication.contains_classifier_code`,
  `compound_group.redundant_singleton`, `action.multivalued`. Treat this RFC as the
  field-view-driven seed for those checks.
- Phase 1 stays **deterministic, no LLM** (same stance as the monograph handoff).
