# RFC: Culinary uses as a first-class plant fact

Status: **Implemented** (2026-06-03) · Author: pipeline notes · Created: 2026-06-03

> Shipped as migration `006_culinary` + `PlantCulinaryUse` model, an
> `ExtractedCulinaryUse` slot in `plant_extractor.py` (under the same grounding
> guard, edibility canonicalized to the 4-value set), persistence in
> `extract_plant_entries_activity`, an `edibility`/`edible_parts` payload + a
> `Съедобность` line in the plants_v2 embedding, and `/api/plants` exposure
> (full monograph field, `edibility`/`edible` list filters, `edibility` facet).

## Problem

The herbalism domain models a plant as a botanical identity plus layered *facts*:
`medicinal_uses`, `compounds`, `harvests`, `habitats`, `toxicities` (see
`backend/app/models/plant.py`). There is **no structured slot for culinary /
edibility knowledge**.

This matters for foraging / wild-food cookbooks (e.g. Замятина, «Кухня
Робинзона»; Замятина, определители съедобных дикоросов). Such a book carries,
per plant, knowledge like:

- which parts are **edible** (молодые листья, корневища, побеги, цветки);
- how they are **prepared as food** (едят сырыми в салатах, отваривают, сушат и
  мелют в муку, квасят, маринуют);
- **seasonality / palatability** caveats (горчат до отваривания, годны только
  молодые побеги);
- **edible-vs-toxic** distinctions (важно для грибов и для ядовитых двойников).

Today this knowledge has nowhere good to live:

- If the book is loaded as **herbalism**, the plant entry is created but edible
  info either lands in free-text `description`, or — worse, before the grounding
  guard — got mislabeled as `medicinal_uses`. It is **not queryable**.
- The dish itself can be captured as a **Recipe** (the herbalism pipeline runs
  `extract_recipes` too), but a recipe is a preparation, not a property of the
  plant. "Which wild plants are edible raw?" cannot be answered from recipes.

## Goal

Let a future agent answer questions like:

- "Какие дикоросы съедобны и какие части у них едят?"
- "Что можно есть сырым, а что только после отваривания?"
- "Чем заменить шпинат из дикорастущих?"
- (fungi) "Какие грибы съедобны, какие условно-съедобны, какие ядовиты?"

…as **structured facts on the plant**, parallel to `medicinal_uses`, with the
same source-layering + verbatim `original_text` + grounding discipline.

## Proposed model

New child table `plant_culinary_uses` (mirrors `PlantMedicinalUse` shape):

| column           | type        | notes                                                        |
|------------------|-------------|--------------------------------------------------------------|
| id               | uuid PK     |                                                              |
| plant_id         | uuid FK     | `ON DELETE CASCADE` (same as other fact tables)              |
| part             | str(50)     | лист / корень / побег / цвет / плод / шляпка (грибы) …        |
| edibility        | str(20)     | controlled: `съедобно` / `условно-съедобно` / `несъедобно` / `ядовито` |
| preparation      | str(60)     | сырым / отварить / сушить / квасить / жарить / мука …         |
| use              | text        | what dish/role: «в салаты», «суп», «заменитель муки» …       |
| season           | text        | when the part is good to gather/eat                          |
| caution          | text        | «горчит до отваривания», «только молодые», ядовитые двойники  |
| original_text    | text        | verbatim source sentence(s) — REQUIRED for grounding         |
| source_book_id   | uuid FK     | `ON DELETE SET NULL`                                          |
| confidence       | float       |                                                              |

`Plant.culinary_uses` relationship + a parallel `ExtractedCulinaryUse` dataclass
in `plant_extractor.py`.

Notes:
- `edibility` is a small controlled vocabulary so we can facet "show edible
  plants". For non-fungi books most rows are `съедобно`; the value earns its keep
  on mushroom guides where `условно-съедобно` / `ядовито` is the whole point.
- Keep `part` free-ish (pass-through) so fungal parts (шляпка/ножка/мякоть) are
  not forced into the plant `_PART_CANON` set.

## Extraction

Extend the plant extractor prompt with a `culinary_uses` array, under the SAME
anti-fabrication grounding guard already added for medicinal facts (each entry
must carry an `original_text` that is verbatim-traceable to the source chunk;
ungrounded entries are dropped). The model must NOT supply edibility from its own
knowledge — only from the text.

## Indexing / API

- Include `culinary_uses` in the plant payload indexed to `plants_v2`.
- Add a facet/filter `edibility` for the catalogue and the future MCP tools.

## Migration

Additive only: one new table + one relationship. No change to existing rows.
Alembic revision creating `plant_culinary_uses`.

## Out of scope (separate, see fungi note)

- **Kingdom discriminator for fungi.** A mushroom atlas is not a plant atlas
  (different biological kingdom). Storing fungi in `plants` is a semantic
  overload that culinary_uses alone does NOT fix. Tracked separately — see
  "fungi / mycology" decision. `culinary_uses.edibility` is useful for fungi but
  is not a substitute for distinguishing kingdom.

## Effort

- Model + Alembic: small.
- Extractor dataclass + prompt + grounding wiring: small (mirrors medicinal_uses).
- Index payload + facet: small.
- Total: ~1 focused change set. Backwards-compatible.
