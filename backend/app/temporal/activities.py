"""Temporal activities — one per pipeline step.

Each activity is a faithful port of the matching ``_bg_*`` background task from
``app/routers/wizard.py``.  Differences:

* No in-memory ``TaskProgress``.  Progress messages go to ``activity.logger``
  and ``activity.heartbeat`` (visible in the Temporal UI / worker logs).
* Each activity is self-loading: it takes only ``book_id`` and re-derives all
  state from the database, so it is safe to retry.
* All DB / LLM / HTTP I/O lives here (never in the workflow), as Temporal
  requires — workflow code must stay deterministic.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete
from temporalio import activity

from app.database import async_session
from app.models.book import Book, BookPage, BookSection, ProcessingLog
from app.models.recipe import Recipe, RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.services import minio as minio_svc
from app.services.ingest import BORN_TEXT_FORMATS, extract_text_from_document
from app.services.preprocessor import split_pdf_smart
from app.services.ocr import ocr_page_with_fallback
from app.services.postprocessor import clean_ocr_text
from app.services.normalizer import normalize_orthography
from app.services.structure_analyzer import analyze_book_structure
from app.services.recipe_extractor import extract_recipes_from_section
from app.services.text_transform import transform_text_chunked
from app.services.embedder import create_embedding
from app.services import qdrant as qdrant_svc

logger = logging.getLogger(__name__)

QDRANT_COLLECTION = "recipes_v2"

# Prompts — copied verbatim from wizard.py so behaviour is identical.
CLEANUP_SYSTEM_PROMPT = (
    "You are cleaning up OCR text from a historical Russian book. "
    "Fix obvious OCR errors, broken words, and artifacts while preserving "
    "the original meaning and style. Keep the original orthography if present "
    "(do NOT modernize spelling — that is a later step). "
    "Return the cleaned text only, no comments."
)

TRANSLATE_SYSTEM_PROMPT = (
    "You are translating a historical Russian text from pre-reform orthography "
    "and archaic language to modern Russian. The text is about herbal tinctures, "
    "distillates, and medicinal preparations. "
    "Modernize archaic words and constructions while preserving the exact meaning. "
    "Keep recipe names, ingredient names, and measurement units recognizable. "
    "Return only the translated text."
)


def _hb(msg: str):
    """Log a progress message and emit a Temporal heartbeat."""
    activity.logger.info(msg)
    try:
        activity.heartbeat(msg)
    except Exception:
        # heartbeat outside an activity context (e.g. unit tests) — ignore
        pass


def _detect_language(sample_text: str) -> tuple[int, float, str]:
    """Classify Russian orthography from a text sample.

    Returns (yat_char_count, final-hard-sign ratio, language). Pre-reform
    Russian shows yat/i-decimal/fita letters and word-final hard signs.
    """
    yat_chars = sum(1 for c in sample_text if c in "ѣѢіІѳѲ")
    words = sample_text.split()
    final_hard = sum(
        1 for w in words if w.strip(".,;:!?()[]«»\"'—-–…").endswith(("ъ", "Ъ"))
    )
    hard_ratio = final_hard / max(1, len(words))
    language = "pre_reform_ru" if (yat_chars > 3 or hard_ratio > 0.08) else "modern_ru"
    return yat_chars, hard_ratio, language


# ──────────────────────────────────────────────────────────────────────
# Step 1: Classify
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def classify_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.file_path:
            raise ValueError("Book has no source file")
        source_format = book.source_format or "pdf"
        file_path = book.file_path

    if source_format in BORN_TEXT_FORMATS:
        # Born-text (txt/docx): text is already in the file, no OCR/PDF split.
        _hb(f"Reading born-text document ({source_format})")
        data = minio_svc.download_file(file_path)
        text = extract_text_from_document(data, source_format)
        pdf_type = "text"
        total_pages = 1
        yat_chars, hard_ratio, language = _detect_language(text[:5000])
        details = {"pdf_type": pdf_type, "language": language, "source_format": source_format,
                   "text_length": len(text), "yat_chars": yat_chars,
                   "final_hard_ratio": round(hard_ratio, 3)}
    else:
        # PDF (incl. DjVu already converted to PDF on upload).
        pdf_bytes = minio_svc.download_file(file_path)
        pages = split_pdf_smart(pdf_bytes)
        total_pages = len(pages)

        check_pages = pages[1:6] if len(pages) > 1 else pages[:5]
        text_pages = sum(1 for p in check_pages if p["page_type"] == "text")
        pdf_type = "text" if text_pages >= len(check_pages) * 0.5 else "image"

        sample_text = " ".join(p["text"] for p in check_pages if p["text"])
        yat_chars, hard_ratio, language = _detect_language(sample_text)
        details = {"pdf_type": pdf_type, "language": language, "source_format": source_format,
                   "pages_checked": len(check_pages), "text_pages": text_pages,
                   "yat_chars": yat_chars, "final_hard_ratio": round(hard_ratio, 3),
                   "total_pages": total_pages}

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.pdf_type = pdf_type
        book.language = language
        book.wizard_step = 2
        book.status = "classified"
        db.add(ProcessingLog(book_id=bid, step="classify", status="completed", details=details))
        await db.commit()

    _hb(f"classified: pdf_type={pdf_type} language={language} pages={total_pages}")
    return {"pdf_type": pdf_type, "language": language, "total_pages": total_pages}


# ──────────────────────────────────────────────────────────────────────
# Step 2: Extract Text
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def extract_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.file_path:
            raise ValueError("Book has no source file")
        file_path = book.file_path
        source_format = book.source_format or "pdf"

    # Born-text (txt/docx): the file already holds text — no PDF split, no OCR.
    if source_format in BORN_TEXT_FORMATS:
        _hb(f"Reading born-text document ({source_format})")
        data = minio_svc.download_file(file_path)
        full_text = extract_text_from_document(data, source_format)
        async with async_session() as db:
            existing = await db.execute(select(BookPage).where(BookPage.book_id == bid))
            for p in existing.scalars().all():
                await db.delete(p)
            await db.flush()
            db.add(BookPage(
                book_id=bid, page_number=1, image_path=None, raw_text=full_text,
                dpi=0, ocr_confidence=100.0, needs_review=False, status="text_extracted",
            ))
            book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
            book.full_text = full_text
            book.wizard_step = 3
            book.status = "extracted"
            db.add(ProcessingLog(book_id=bid, step="extract", status="completed",
                                 details={"total_pages": 1, "text_length": len(full_text),
                                          "source_format": source_format}))
            await db.commit()
        _hb(f"extracted: born-text {source_format}, {len(full_text)} chars")
        return {"total_pages": 1, "text_length": len(full_text)}

    async with async_session() as db:
        _hb("Downloading PDF from storage")
        pdf_bytes = minio_svc.download_file(file_path)
        _hb("Splitting PDF into pages")
        pages = split_pdf_smart(pdf_bytes)
        _hb(f"Found {len(pages)} pages")

        existing = await db.execute(select(BookPage).where(BookPage.book_id == bid))
        for p in existing.scalars().all():
            await db.delete(p)
        await db.flush()

        all_texts = []
        for i, page_data in enumerate(pages):
            page_num = page_data["page_number"]
            page_type = page_data["page_type"]
            _hb(f"Page {page_num}/{len(pages)} ({page_type})")

            image_path = None
            raw_text = page_data["text"]
            confidence = 100.0 if page_type == "text" else None
            method = "pdf_extract" if page_type == "text" else None
            status = "text_extracted" if page_type == "text" else "needs_ocr"

            if page_type == "image" and page_data["image_bytes"]:
                image_path = f"books/{bid}/pages/{page_num:04d}.png"
                minio_svc.upload_file(page_data["image_bytes"], image_path, content_type="image/png")
                text, conf, ocr_method = await ocr_page_with_fallback(page_data["image_bytes"])
                raw_text = text
                confidence = conf
                method = ocr_method
                status = "ocr_done"

            db.add(BookPage(
                book_id=bid, page_number=page_num, image_path=image_path,
                raw_text=raw_text, dpi=page_data["dpi"], ocr_confidence=confidence,
                needs_review=confidence is not None and confidence < 60.0, status=status,
            ))
            if raw_text:
                all_texts.append(raw_text)

        full_text = "\n\n".join(all_texts)
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = full_text
        book.wizard_step = 3
        book.status = "extracted"
        db.add(ProcessingLog(book_id=bid, step="extract", status="completed",
                             details={"total_pages": len(pages), "text_length": len(full_text)}))
        await db.commit()

    _hb(f"extracted: {len(pages)} pages, {len(full_text)} chars")
    return {"total_pages": len(pages), "text_length": len(full_text)}


# ──────────────────────────────────────────────────────────────────────
# Step 2b: Cleanup
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def cleanup_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text to clean up. Run extract first.")
        full_text = book.full_text
        pdf_type = book.pdf_type
        language = book.language

    _hb("Applying rule-based cleanup")
    cleaned = clean_ocr_text(full_text)

    use_llm = pdf_type == "image" or language == "pre_reform_ru"
    if use_llm:
        _hb(f"Sending to LLM for OCR artifact cleanup (chunked, {len(cleaned)} chars)")
        cleaned = await transform_text_chunked(
            cleaned, system_prompt=CLEANUP_SYSTEM_PROMPT, task="text_cleanup", cb=_hb,
        )
        _hb("LLM cleanup complete")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = cleaned
        db.add(ProcessingLog(book_id=bid, step="cleanup", status="completed",
                             details={"text_length": len(cleaned), "used_llm": use_llm}))
        await db.commit()

    return {"text_length": len(cleaned), "used_llm": use_llm}


# ──────────────────────────────────────────────────────────────────────
# Step 3: Translate (conditional — no-op for modern Russian)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def translate_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text to translate. Run extract first.")
        if book.language != "pre_reform_ru":
            book.wizard_step = 4
            await db.commit()
            _hb("translate skipped: book is modern Russian")
            return {"status": "skipped", "reason": "modern Russian"}
        full_text = book.full_text

    _hb("Applying rule-based orthographic normalization")
    normalized = normalize_orthography(full_text)
    _hb(f"Normalized: {len(normalized)} chars")

    _hb(f"Sending to LLM for archaic language translation (chunked, {len(normalized)} chars)")
    translated = await transform_text_chunked(
        normalized, system_prompt=TRANSLATE_SYSTEM_PROMPT, task="translation", cb=_hb,
    )
    _hb(f"Translated: {len(translated)} chars")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = translated
        book.wizard_step = 4
        db.add(ProcessingLog(book_id=bid, step="translate", status="completed",
                             details={"original_length": len(normalized), "translated_length": len(translated)}))
        await db.commit()

    return {"status": "translated", "original_length": len(normalized), "translated_length": len(translated)}


# ──────────────────────────────────────────────────────────────────────
# Step 4: Analyze Structure
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def analyze_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text to analyze. Run extract first.")
        full_text = book.full_text
        title = book.title
        year = book.year

    _hb(f"Text: {len(full_text)} chars, {full_text.count(chr(10))+1} lines")
    sections = await analyze_book_structure(
        full_text, book_title=title, book_year=year, progress_callback=_hb,
    )
    _hb(f"Found {len(sections)} sections")

    async with async_session() as db:
        await db.execute(delete(BookSection).where(BookSection.book_id == bid))
        await db.flush()

        lines = full_text.split("\n")
        recipe_blocks = 0
        for s in sections:
            start_char = sum(len(lines[i]) + 1 for i in range(min(s.start_line - 1, len(lines))))
            end_char = sum(len(lines[i]) + 1 for i in range(min(s.end_line, len(lines))))
            section_text = "\n".join(lines[max(0, s.start_line - 1):min(s.end_line, len(lines))])
            preview = section_text[:200] + "..." if len(section_text) > 200 else section_text
            if s.section_type == "recipe_block":
                recipe_blocks += 1

            db.add(BookSection(
                book_id=bid, section_type=s.section_type, title=s.title,
                start_line=s.start_line, end_line=s.end_line,
                start_char=start_char, end_char=end_char,
                content_preview=preview, recipe_pattern=s.recipe_pattern,
                estimated_recipe_count=s.estimated_recipe_count, confidence=s.confidence,
            ))

        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 5
        book.status = "analyzed"
        db.add(ProcessingLog(book_id=bid, step="analyze", status="completed",
                             details={"sections_found": len(sections), "recipe_blocks": recipe_blocks}))
        await db.commit()

    return {"sections_found": len(sections), "recipe_blocks": recipe_blocks}


# ──────────────────────────────────────────────────────────────────────
# Step 5: Extract Recipes
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def extract_recipes_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text available")
        book_title = book.title
        full_text = book.full_text

        result = await db.execute(
            select(BookSection).where(
                BookSection.book_id == bid,
                BookSection.section_type == "recipe_block",
            ).order_by(BookSection.start_line)
        )
        sections = result.scalars().all()
        if not sections:
            raise ValueError("No recipe sections found. Run analyze first.")

        lines = full_text.split("\n")
        section_data = []
        for s in sections:
            section_lines = lines[max(0, s.start_line - 1):min(s.end_line, len(lines))]
            section_data.append({
                "text": "\n".join(section_lines),
                "title": s.title,
                "recipe_pattern": s.recipe_pattern,
            })

    _hb(f"Processing {len(section_data)} recipe sections")

    async with async_session() as db:
        await db.execute(delete(Recipe).where(Recipe.book_id == bid))
        await db.flush()

        recipes_count = 0
        failed_sections = 0
        for i, sd in enumerate(section_data):
            _hb(f"Section {i+1}/{len(section_data)}: {sd['title']} ({len(sd['text'])} chars)")
            try:
                extracted = await extract_recipes_from_section(
                    sd["text"], book_title=book_title,
                    recipe_pattern=sd["recipe_pattern"], progress_callback=_hb,
                )
            except Exception as e:
                failed_sections += 1
                logger.exception(f"Section {i+1} extraction failed")
                _hb(f"Section {i+1}: ERROR - {e} (skipped)")
                continue

            _hb(f"Section {i+1}: extracted {len(extracted)} recipes")
            for er in extracted:
                recipe = Recipe(
                    book_id=bid, name=er.name, category=er.category,
                    original_text=er.original_text, qdrant_collection=QDRANT_COLLECTION,
                )
                db.add(recipe)
                await db.flush()
                for ing in er.ingredients:
                    db.add(RecipeIngredient(
                        recipe_id=recipe.id, name=ing.name,
                        original_name=ing.original, amount=ing.amount, unit=ing.unit,
                    ))
                recipes_count += 1

        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 6
        book.status = "recipes_extracted"
        db.add(ProcessingLog(book_id=bid, step="extract_recipes", status="completed",
                             details={"recipes_count": recipes_count,
                                      "sections_processed": len(section_data),
                                      "sections_failed": failed_sections}))
        await db.commit()

    return {"recipes_count": recipes_count, "sections_processed": len(section_data),
            "sections_failed": failed_sections}


# ──────────────────────────────────────────────────────────────────────
# Step 6: Match Ingredients
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def match_ingredients_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        recipes = (await db.execute(select(Recipe).where(Recipe.book_id == bid))).scalars().all()
        if not recipes:
            raise ValueError("No recipes found")

        _hb(f"Matching ingredients for {len(recipes)} recipes")

        all_ingredients = (await db.execute(select(Ingredient))).scalars().all()
        all_synonyms = (await db.execute(select(IngredientSynonym))).scalars().all()
        name_to_ingredient = {i.canonical_name.lower(): i for i in all_ingredients}
        synonym_to_ingredient = {s.synonym.lower(): s.ingredient_id for s in all_synonyms}

        matched = 0
        new_created = 0
        for ri_idx, recipe in enumerate(recipes):
            recipe_ingredients = (await db.execute(
                select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
            )).scalars().all()

            for ri in recipe_ingredients:
                name_lower = ri.name.lower().strip()
                if not name_lower:
                    continue
                if name_lower in name_to_ingredient:
                    ri.ingredient_id = name_to_ingredient[name_lower].id
                    matched += 1
                elif name_lower in synonym_to_ingredient:
                    ri.ingredient_id = synonym_to_ingredient[name_lower]
                    matched += 1
                else:
                    new_ing = Ingredient(canonical_name=ri.name.strip(), category="other")
                    db.add(new_ing)
                    await db.flush()
                    ri.ingredient_id = new_ing.id
                    name_to_ingredient[name_lower] = new_ing
                    new_created += 1

            if (ri_idx + 1) % 5 == 0 or ri_idx == len(recipes) - 1:
                _hb(f"Recipe {ri_idx+1}/{len(recipes)}: matched={matched}, new={new_created}")

        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 7
        db.add(ProcessingLog(book_id=bid, step="match_ingredients", status="completed",
                             details={"matched": matched, "new_created": new_created}))
        await db.commit()

    return {"matched": matched, "new_created": new_created, "total": matched + new_created}


# ──────────────────────────────────────────────────────────────────────
# Step 7: Index to Qdrant
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def index_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()

        _hb("Ensuring Qdrant collection exists")
        await qdrant_svc.ensure_collection(QDRANT_COLLECTION)
        await qdrant_svc.delete_by_filter(QDRANT_COLLECTION, "book_id", str(bid))

        recipes = (await db.execute(select(Recipe).where(Recipe.book_id == bid))).scalars().all()
        if not recipes:
            raise ValueError("No recipes to index")

        _hb(f"Indexing {len(recipes)} recipes")
        points = []
        for i, recipe in enumerate(recipes):
            ingredients = [ri.name for ri in (await db.execute(
                select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
            )).scalars().all()]

            embed_text = f"Рецепт: {recipe.name}\n\nСодержание: {recipe.original_text or ''}"
            _hb(f"Embedding {i+1}/{len(recipes)}: {recipe.name}")
            embedding = await create_embedding(embed_text)

            point = {
                "id": str(recipe.id),
                "dense": embedding["dense"],
                "payload": {
                    "recipe_name": recipe.name,
                    "category": recipe.category or "",
                    "source_book": book.title,
                    "book_id": str(bid),
                    "author": book.author or "",
                    "year": book.year,
                    "ingredients": ingredients,
                    "content": recipe.original_text or "",
                    "language": book.language or "modern_ru",
                },
            }
            if "sparse" in embedding and embedding["sparse"]:
                point["sparse"] = embedding["sparse"]
            points.append(point)

            recipe.qdrant_point_id = str(recipe.id)
            recipe.qdrant_collection = QDRANT_COLLECTION
            recipe.indexed_at = datetime.now(timezone.utc)

        _hb("Upserting points to Qdrant")
        for i in range(0, len(points), 50):
            await qdrant_svc.upsert_points(QDRANT_COLLECTION, points[i:i + 50])
            _hb(f"Upserted batch {i//50 + 1}")

        book.wizard_step = 8
        book.status = "indexed"
        db.add(ProcessingLog(book_id=bid, step="index", status="completed",
                             details={"points_indexed": len(points), "collection": QDRANT_COLLECTION}))
        await db.commit()

    return {"points_indexed": len(points), "collection": QDRANT_COLLECTION}


# ──────────────────────────────────────────────────────────────────────
# De-risk: trivial ping activity (connectivity smoke test)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def ping_activity(name: str) -> str:
    _hb(f"ping received: {name}")
    return f"pong: {name}"
