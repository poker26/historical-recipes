# Plan: Book Processing Wizard

## Context

Current pipeline (n8n + regex parsers) proved unworkable:
- n8n gives no visibility/interactivity during processing
- Universal regex parser fails — each book has unique recipe formatting
- Bibliography, TOC, author lists get misclassified as recipes
- No LLM involvement in structure analysis or recipe extraction

The user needs a **wizard-style UI** where each processing step is visible, reviewable, and re-runnable. Quality over speed/cost. LLM should do the heavy lifting for understanding book structure and extracting recipes.

**Existing MVP** (Telegram bot + n8n hybrid search) works well and must not be broken. New processing writes to a new Qdrant collection.

---

## Architecture: Step-by-Step Wizard

No n8n for the main pipeline or vectorization. n8n stays only for Telegram bot. Everything else (processing, search, indexing) moves to the backend. Frontend drives the wizard, calling backend endpoints per step. Each step saves results to DB, so the user can review, correct, and re-run any step.

**LLM strategy**: Send entire book text to LLM if it fits (Gemini 2.5 Pro has 1M token context ≈ ~500K chars Russian). For books that don't fit — split into overlapping chunks (~50 pages, 2-page overlap). Most historical recipe books are 200-600 pages, so many will fit in a single call.

### Step 1: Upload & Classify
- Upload PDF to MinIO
- Auto-detect: text PDF vs image PDF (check first 5 non-cover pages for extractable text)
- Set `book.pdf_type` = `text` | `image`
- Set `book.language` = `modern_ru` | `pre_reform_ru` (detect ѣ, і, ѳ in extracted text)
- **UI**: Upload form with auto-detected fields (editable), book metadata

### Step 2: Extract Text
- **Text PDF**: Extract via PyMuPDF, save per-page text
- **Image PDF**: OCR each page (Tesseract + LLM fallback), then **LLM cleanup** of entire text
- Save cleaned text as the permanent asset (can discard images after)
- **UI**: Page-by-page view with extracted text, confidence scores, ability to edit text manually
- **LLM**: `gemini-flash` for OCR fallback, `gemini-pro` for hard pages, `qwen3-235b` for text cleanup

### Step 3: Translate (conditional)
- Only for `pre_reform_ru` books
- LLM translates old orthography + archaic words to modern Russian
- Keep original text alongside translation
- **UI**: Side-by-side original vs translated, ability to edit
- **LLM**: `qwen3-235b` (large context, good with Russian)

### Step 4: Analyze Structure (LLM)
- **Key innovation**: Send ENTIRE book text to LLM (Gemini 2.5 Pro, 1M context), ask it to identify:
  - Where recipes start/end
  - Where bibliography, TOC, introductions, appendices are
  - The formatting pattern used for recipes in THIS book
- If book > 500K chars: split into overlapping chunks (~50 pages, 2-page overlap), merge results
- LLM returns structured JSON with section boundaries and types
- **UI**: Scrollable list of detected sections with type badges, title, page range, text preview. Click to expand. Dropdown to change section type. Delete button for false positives
- **LLM**: `gemini-2.5-pro` (via OpenRouter) — 1M context, strong at structure analysis

### Step 5: Extract Recipes (LLM-assisted)
- For each identified recipe section, LLM extracts structured data:
  - Name, category, full text, ingredients (name, amount, unit)
- LLM works with the ACTUAL recipe text, not regex patterns
- **UI**: List of extracted recipes with details, ability to edit/delete/add
- **LLM**: `qwen3-235b` for extraction

### Step 6: Ingredients & Dictionary
- Match extracted ingredients against existing dictionary (fuzzy + lemmatization)
- Flag new/unknown ingredients for review
- Handle synonyms (корица = цейлонская корица = коричное дерево)
- Handle morphology (корицы, корицу, корицей → корица)
- **UI**: Ingredient list with match status (matched/new/ambiguous), ability to merge, rename, link to plants
- **DB**: `ingredients` table grows incrementally across books

### Step 7: Vectorize & Index
- Generate BGE-M3 embeddings (dense + sparse) for each recipe
- Upsert to NEW Qdrant collection `recipes_v2` (same schema as existing: dense 1024D cosine + sparse IDF)
- Rich payload metadata: book_id, book_title, author, year, recipe_name, category, source_book, ingredients list
- **UI**: Index status, ability to re-index individual recipes

---

## Backend Endpoints

### New router: `backend/app/routers/wizard.py`

```
POST /api/wizard/{book_id}/classify          → Step 1: detect pdf_type + language
POST /api/wizard/{book_id}/extract           → Step 2: extract/OCR all pages
POST /api/wizard/{book_id}/extract/{page}    → Step 2: re-extract single page
POST /api/wizard/{book_id}/cleanup           → Step 2b: LLM cleanup of OCR text
POST /api/wizard/{book_id}/translate         → Step 3: translate pre-reform text
POST /api/wizard/{book_id}/analyze           → Step 4: LLM structure analysis
PUT  /api/wizard/{book_id}/structure         → Step 4: save corrected structure
POST /api/wizard/{book_id}/extract-recipes   → Step 5: LLM recipe extraction
PUT  /api/wizard/{book_id}/recipes/{id}      → Step 5: edit extracted recipe
POST /api/wizard/{book_id}/match-ingredients → Step 6: match against dictionary
PUT  /api/wizard/{book_id}/ingredients       → Step 6: save ingredient corrections
POST /api/wizard/{book_id}/index             → Step 7: vectorize & index to Qdrant
GET  /api/wizard/{book_id}/status            → Current wizard state (which step, progress)
```

### New service: `backend/app/services/structure_analyzer.py`
- `analyze_book_structure(text) -> list[Section]` — LLM-based structure detection
- Section types: recipe_block, bibliography, toc, introduction, appendix, chapter_header, other

### New service: `backend/app/services/recipe_extractor.py`
- `extract_recipes_llm(text, section_info) -> list[ExtractedRecipe]` — LLM-based recipe extraction
- Returns structured recipes with ingredients already parsed

### Modified service: `backend/app/services/llm.py`
- Add JSON mode support (structured output)
- Add chunked processing for long texts (split into overlapping windows)
- Add task types: `structure_analysis`, `recipe_extraction`, `text_cleanup`, `translation`

---

## Database Changes

### New table: `book_sections`
```
id, book_id, section_type, title, start_page, end_page, start_char, end_char,
content_preview, confidence, manually_verified, created_at
```

### New table: `ingredients` (global dictionary)
```
id, canonical_name, category (plant/spice/liquid/mineral/other),
plant_id (FK to plants), created_at
```

### New table: `ingredient_synonyms`
```
id, ingredient_id (FK), synonym, language, source_book_id
```

### Modify `book_chunks`:
- Add `section_id` FK to `book_sections`

### Modify `recipe_ingredients`:
- Add `ingredient_id` FK to global `ingredients` table

### Modify `books`:
- Add `wizard_step` field (tracks current wizard position: 1-7)
- Add `full_text` field (cleaned combined text — the asset)

---

## Frontend: Wizard UI

### New page: `frontend/src/app/books/[id]/wizard/page.tsx`

Step indicator bar at top (1→2→3→4→5→6→7), showing current step.

Each step is a panel with:
- Results display (what was detected/extracted)
- Edit controls (correct mistakes)
- "Re-run" button (re-process this step)
- "Next" button (proceed to next step)
- "Back" button (go back and re-do)

---

## LLM Model Selection

| Step | Model | Why |
|------|-------|-----|
| OCR fallback (medium) | gemini-2.5-flash | Fast, cheap, good for images |
| OCR fallback (hard) | gemini-2.5-pro | Best for degraded scans |
| Text cleanup | qwen3-235b | Good Russian, large context |
| Translation | qwen3-235b | Understands pre-reform Russian well |
| Structure analysis | gemini-2.5-pro | 1M context, fits entire book |
| Recipe extraction | gemini-2.5-pro | Precise structured extraction |
| Ingredient matching | qwen3-32b | Lightweight, many small calls |

## LLM Prompt Strategy

### Step 4 — Structure Analysis (Gemini 2.5 Pro, entire book)
```
System: You are analyzing the structure of a historical Russian book about herbal tinctures, 
distillates, and medicinal preparations. Your task is to identify what sections the book contains.

User: Here is the complete text of the book "{title}" ({year}).

Identify ALL sections of the book. For each section, determine its type:
- recipe_block: a contiguous group of actual recipes with ingredients and preparation instructions
- bibliography: lists of references, authors, publications  
- toc: table of contents
- introduction: foreword, preface, general descriptions
- appendix: tables (spirit content, temperatures, densities), indexes
- chapter_header: chapter/section dividers
- other: anything that doesn't fit above

For recipe_block sections, also identify the formatting pattern used (e.g., "**N. Title**", 
"§ N. Title", "N. Title", or describe the pattern).

Return a JSON array. Each element:
{
  "type": "recipe_block|bibliography|toc|introduction|appendix|chapter_header|other",
  "title": "section title or description",
  "start_line": <first line number>,
  "end_line": <last line number>,
  "recipe_pattern": "pattern description (only for recipe_block)",
  "estimated_recipe_count": <number (only for recipe_block)>,
  "confidence": 0.0-1.0
}
```

### Step 5 — Recipe Extraction (Gemini 2.5 Pro, per recipe_block section)
```
System: Extract individual recipes from this section of a historical Russian recipe book.
Be PRECISE — extract only actual recipes (with ingredients/instructions), not descriptions, 
chapter headers, footnotes, or references.

User: Here is a recipe section from "{book_title}". 
The recipes in this section use the pattern: {recipe_pattern}.

Extract each recipe as JSON:
{
  "name": "recipe name",
  "category": "водка|ликёр|настойка|бальзам|масло|вода|эссенция|эликсир|тинктура|ратафия|розолия|другое",
  "original_text": "full original recipe text",
  "ingredients": [
    {"name": "ingredient name (nominative case)", "amount": "number", "unit": "unit name", "original": "as written in text"}
  ]
}

Return JSON array of all extracted recipes.
```

---

## Phased Implementation

### Phase 1 (MVP wizard — get value fast)
1. Wizard UI skeleton with step navigation
2. Step 1: Upload & classify (reuse existing upload + add auto-detection)
3. Step 2: Extract text (reuse existing split_pdf_smart + OCR)
4. Step 4: LLM structure analysis (new — core value)
5. Step 5: LLM recipe extraction (new — core value)
6. Step 7: Index to Qdrant (reuse existing embedder + qdrant services)

### Phase 2 (quality & dictionary)
7. Step 3: Translation for pre-reform texts
8. Step 6: Ingredient dictionary with matching
9. Manual edit capabilities at each step
10. Re-run individual steps

### Phase 3 (polish)
11. Page-level text editing in Step 2
12. Visual structure map in Step 4
13. Ingredient synonym management
14. Batch re-indexing

---

## Files to Modify/Create

### Create:
- `backend/app/routers/wizard.py` — wizard endpoints
- `backend/app/services/structure_analyzer.py` — LLM structure analysis
- `backend/app/services/recipe_extractor.py` — LLM recipe extraction
- `backend/app/models/section.py` — BookSection model
- `backend/app/models/ingredient.py` — Ingredient, IngredientSynonym models
- `frontend/src/app/books/[id]/wizard/page.tsx` — wizard UI
- `frontend/src/components/WizardSteps.tsx` — step components
- New alembic migration for schema changes

### Modify:
- `backend/app/services/llm.py` — add JSON mode, chunked processing, new task types
- `backend/app/models/book.py` — add wizard_step, full_text fields
- `backend/app/models/recipe.py` — add ingredient_id FK
- `backend/app/main.py` — register wizard router
- `frontend/src/lib/api.ts` — add wizard API methods
- `frontend/src/app/books/[id]/page.tsx` — add "Open Wizard" button

### Keep as-is:
- `backend/app/services/embedder.py` — works well, used directly in wizard step 7
- `backend/app/services/qdrant.py` — works well, ensure_collection for recipes_v2, search used by search router
- `backend/app/services/ocr.py` — works well
- `backend/app/services/preprocessor.py` — split_pdf_smart works
- `backend/app/services/minio.py` — works
- `backend/app/routers/search.py` — already in backend, no n8n needed
- n8n Telegram bot workflow — keeps working with existing Qdrant collection

### Deprecate (keep but don't use in wizard):
- `backend/app/routers/pipeline.py` — replaced by wizard.py
- `backend/app/services/parser.py` — replaced by LLM extraction
- `backend/app/services/postprocessor.py` detect_chunk_boundaries — replaced by LLM analysis
- n8n Book Processing Pipeline workflow — replaced by wizard
- n8n vectorization workflows — replaced by backend indexing endpoint

---

## Qdrant Collection: `recipes_v2`

Same config as MVP:
```json
{
  "vectors": { "dense": { "size": 1024, "distance": "Cosine" } },
  "sparse_vectors": { "sparse": { "modifier": "idf" } }
}
```

Payload per point:
```json
{
  "recipe_name": "Анисовая водка",
  "category": "водка",
  "source_book": "Полный самогонщик и дистиллятор",
  "book_id": "uuid",
  "author": "И. Морев",
  "year": 1868,
  "ingredients": ["анис", "водка", "сахар"],
  "content": "full recipe text",
  "language": "modern_ru"
}
```

Index fields: `category`, `source_book`, `recipe_name`, `book_id`

---

## Verification

1. Upload a test PDF (already have 2 books in the system)
2. Walk through wizard steps 1→7
3. At step 4, verify LLM correctly identifies recipe sections vs bibliography
4. At step 5, verify extracted recipes match actual content
5. At step 7, verify points appear in Qdrant `recipes_v2` collection
6. Test hybrid search via existing Search Bench UI against new collection
