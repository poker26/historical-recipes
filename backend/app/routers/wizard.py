"""Book processing wizard: step-by-step interactive pipeline.

Each endpoint corresponds to a wizard step. Frontend calls them sequentially,
with the user reviewing and optionally correcting results between steps.

Steps:
1. classify   — detect pdf_type (text/image) and language
2. extract    — extract text from PDF pages (PyMuPDF or OCR)
   cleanup    — LLM cleanup of OCR text (for image PDFs)
3. translate  — translate pre-reform Russian (conditional)
4. analyze    — LLM structure analysis (find recipe blocks, bibliography, etc.)
5. extract-recipes — LLM recipe extraction from identified sections
6. match-ingredients — match ingredients against global dictionary
7. index      — vectorize and index to Qdrant
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.book import Book, BookPage, BookSection, BookChunk, ProcessingLog
from app.models.recipe import Recipe, RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.services import minio as minio_svc
from app.services.preprocessor import split_pdf_smart
from app.services.ocr import ocr_page, ocr_page_with_fallback
from app.services.postprocessor import clean_ocr_text
from app.services.normalizer import normalize_orthography
from app.services.structure_analyzer import analyze_book_structure
from app.services.recipe_extractor import extract_recipes_from_section
from app.services.llm import chat_completion, chat_completion_json
from app.services.embedder import create_embedding
from app.services import qdrant as qdrant_svc

router = APIRouter()

MIN_TEXT_LENGTH = 50  # minimum chars to consider a page as having text


async def _get_book(book_id: uuid.UUID, db: AsyncSession) -> Book:
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


# ─── Step 1: Classify ───────────────────────────────────────────────

@router.post("/{book_id}/classify")
async def classify_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Detect PDF type (text/image) and language (modern_ru/pre_reform_ru).

    Checks first 5 non-cover pages for extractable text.
    Detects pre-reform characters (ѣ, і, ѳ) in extracted text.
    """
    book = await _get_book(book_id, db)
    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no PDF file")

    pdf_bytes = minio_svc.download_file(book.file_path)
    pages = split_pdf_smart(pdf_bytes)

    # Check pages 2-6 (skip cover)
    check_pages = pages[1:6] if len(pages) > 1 else pages[:5]
    text_pages = sum(1 for p in check_pages if p["page_type"] == "text")

    # If majority of checked pages have text → text PDF
    pdf_type = "text" if text_pages >= len(check_pages) * 0.5 else "image"

    # Detect language from extracted text
    sample_text = " ".join(p["text"] for p in check_pages if p["text"])
    pre_reform_chars = sum(1 for c in sample_text if c in "ѣѢіІѳѲ")
    language = "pre_reform_ru" if pre_reform_chars > 3 else "modern_ru"

    book.pdf_type = pdf_type
    book.language = language
    book.wizard_step = 2
    book.status = "classified"

    log = ProcessingLog(book_id=book_id, step="classify", status="completed",
                        details={"pdf_type": pdf_type, "language": language,
                                 "pages_checked": len(check_pages), "text_pages": text_pages,
                                 "total_pages": len(pages)})
    db.add(log)
    await db.commit()

    return {
        "status": "ok",
        "pdf_type": pdf_type,
        "language": language,
        "total_pages": len(pages),
        "text_pages_in_sample": text_pages,
        "sample_size": len(check_pages),
    }


# ─── Step 2: Extract Text ───────────────────────────────────────────

@router.post("/{book_id}/extract")
async def extract_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Extract text from all pages.

    Text PDFs: extract via PyMuPDF directly.
    Image PDFs: OCR each page (Tesseract + LLM fallback).
    Saves per-page text and combined full_text on the book.
    """
    book = await _get_book(book_id, db)
    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no PDF file")

    pdf_bytes = minio_svc.download_file(book.file_path)
    pages = split_pdf_smart(pdf_bytes)

    # Delete existing pages
    existing = await db.execute(select(BookPage).where(BookPage.book_id == book_id))
    for p in existing.scalars().all():
        await db.delete(p)
    await db.flush()

    page_results = []
    all_texts = []

    for page_data in pages:
        page_num = page_data["page_number"]
        page_type = page_data["page_type"]

        image_path = None
        raw_text = page_data["text"]
        confidence = 100.0 if page_type == "text" else None
        method = "pdf_extract" if page_type == "text" else None
        status = "text_extracted" if page_type == "text" else "needs_ocr"

        if page_type == "image" and page_data["image_bytes"]:
            image_path = f"books/{book_id}/pages/{page_num:04d}.png"
            minio_svc.upload_file(page_data["image_bytes"], image_path, content_type="image/png")

            # OCR immediately
            text, conf, ocr_method = await ocr_page_with_fallback(page_data["image_bytes"])
            raw_text = text
            confidence = conf
            method = ocr_method
            status = "ocr_done"

        page = BookPage(
            book_id=book_id,
            page_number=page_num,
            image_path=image_path,
            raw_text=raw_text,
            dpi=page_data["dpi"],
            ocr_confidence=confidence,
            needs_review=confidence is not None and confidence < 60.0,
            status=status,
        )
        db.add(page)

        if raw_text:
            all_texts.append(raw_text)

        page_results.append({
            "page_number": page_num,
            "page_type": page_type,
            "text_length": len(raw_text) if raw_text else 0,
            "confidence": round(confidence, 1) if confidence else None,
            "method": method,
            "needs_review": page.needs_review,
        })

    # Combine all text and save as book asset
    full_text = "\n\n".join(all_texts)
    book.full_text = full_text
    book.wizard_step = 3
    book.status = "extracted"

    log = ProcessingLog(book_id=book_id, step="extract", status="completed",
                        details={"total_pages": len(pages), "text_length": len(full_text)})
    db.add(log)
    await db.commit()

    return {
        "status": "ok",
        "total_pages": len(pages),
        "text_length": len(full_text),
        "pages": page_results,
    }


@router.post("/{book_id}/cleanup")
async def cleanup_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """LLM cleanup of OCR text. Sends text to LLM for artifact removal and readability improvement.

    Only meaningful for image PDFs where OCR produced noisy text.
    """
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to clean up. Run extract first.")

    # First apply rule-based cleanup
    cleaned = clean_ocr_text(book.full_text)

    # Then LLM cleanup for OCR artifacts
    if book.pdf_type == "image":
        messages = [
            {"role": "system", "content": (
                "You are cleaning up OCR text from a historical Russian book. "
                "Fix obvious OCR errors, broken words, and artifacts while preserving "
                "the original meaning and style. Keep the original orthography (ѣ, і, ъ) "
                "if present. Do NOT add, remove, or rephrase content — only fix OCR damage. "
                "Return the cleaned text only, no comments."
            )},
            {"role": "user", "content": cleaned},
        ]
        cleaned = await chat_completion(
            messages, task="text_cleanup", temperature=0.1, max_tokens=32768,
        )

    book.full_text = cleaned
    log = ProcessingLog(book_id=book_id, step="cleanup", status="completed",
                        details={"text_length": len(cleaned), "used_llm": book.pdf_type == "image"})
    db.add(log)
    await db.commit()

    return {"status": "ok", "text_length": len(cleaned), "used_llm": book.pdf_type == "image"}


# ─── Step 3: Translate ──────────────────────────────────────────────

@router.post("/{book_id}/translate")
async def translate_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Translate pre-reform Russian to modern Russian.

    Applies rule-based orthographic normalization first (ѣ→е, і→и, ѳ→ф),
    then LLM for archaic words and constructions.
    Stores both original and translated text.
    """
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to translate. Run extract first.")

    if book.language != "pre_reform_ru":
        return {"status": "skipped", "reason": "Book is modern Russian, no translation needed"}

    # Rule-based normalization
    normalized = normalize_orthography(book.full_text)

    # LLM translation of archaic constructions
    messages = [
        {"role": "system", "content": (
            "You are translating a historical Russian text from pre-reform orthography "
            "and archaic language to modern Russian. The text is about herbal tinctures, "
            "distillates, and medicinal preparations. "
            "Modernize archaic words and constructions while preserving the exact meaning. "
            "Keep recipe names, ingredient names, and measurement units recognizable. "
            "Return only the translated text."
        )},
        {"role": "user", "content": normalized},
    ]
    translated = await chat_completion(
        messages, task="translation", temperature=0.1, max_tokens=32768,
    )

    # Keep original in pages, update full_text with translation
    book.full_text = translated
    book.wizard_step = 4

    log = ProcessingLog(book_id=book_id, step="translate", status="completed",
                        details={"original_length": len(normalized), "translated_length": len(translated)})
    db.add(log)
    await db.commit()

    return {"status": "ok", "original_length": len(normalized), "translated_length": len(translated)}


# ─── Step 4: Analyze Structure ───────────────────────────────────────

@router.post("/{book_id}/analyze")
async def analyze_structure(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """LLM-based structure analysis. Identifies sections: recipe blocks, bibliography, TOC, etc."""
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to analyze. Run extract first.")

    sections = await analyze_book_structure(
        book.full_text,
        book_title=book.title,
        book_year=book.year,
    )

    # Delete existing sections
    await db.execute(delete(BookSection).where(BookSection.book_id == book_id))
    await db.flush()

    lines = book.full_text.split("\n")
    section_results = []

    for s in sections:
        # Calculate char offsets from line numbers
        start_char = sum(len(lines[i]) + 1 for i in range(min(s.start_line - 1, len(lines))))
        end_char = sum(len(lines[i]) + 1 for i in range(min(s.end_line, len(lines))))

        # Content preview: first 200 chars of the section
        section_text = "\n".join(lines[max(0, s.start_line - 1):min(s.end_line, len(lines))])
        preview = section_text[:200] + "..." if len(section_text) > 200 else section_text

        db_section = BookSection(
            book_id=book_id,
            section_type=s.section_type,
            title=s.title,
            start_line=s.start_line,
            end_line=s.end_line,
            start_char=start_char,
            end_char=end_char,
            content_preview=preview,
            recipe_pattern=s.recipe_pattern,
            estimated_recipe_count=s.estimated_recipe_count,
            confidence=s.confidence,
        )
        db.add(db_section)
        await db.flush()

        section_results.append({
            "id": str(db_section.id),
            "section_type": s.section_type,
            "title": s.title,
            "start_line": s.start_line,
            "end_line": s.end_line,
            "content_preview": preview,
            "recipe_pattern": s.recipe_pattern,
            "estimated_recipe_count": s.estimated_recipe_count,
            "confidence": s.confidence,
        })

    book.wizard_step = 5
    book.status = "analyzed"
    log = ProcessingLog(book_id=book_id, step="analyze", status="completed",
                        details={"sections_found": len(sections),
                                 "recipe_blocks": sum(1 for s in sections if s.section_type == "recipe_block")})
    db.add(log)
    await db.commit()

    return {"status": "ok", "sections": section_results}


class SectionUpdate(BaseModel):
    section_type: str
    title: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@router.put("/{book_id}/sections/{section_id}")
async def update_section(
    book_id: uuid.UUID,
    section_id: uuid.UUID,
    update: SectionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a section's type or boundaries (manual correction)."""
    result = await db.execute(
        select(BookSection).where(BookSection.id == section_id, BookSection.book_id == book_id)
    )
    section = result.scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    section.section_type = update.section_type
    if update.title is not None:
        section.title = update.title
    if update.start_line is not None:
        section.start_line = update.start_line
    if update.end_line is not None:
        section.end_line = update.end_line
    section.manually_verified = True

    await db.commit()
    return {"status": "ok"}


@router.delete("/{book_id}/sections/{section_id}")
async def delete_section(
    book_id: uuid.UUID,
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a section (false positive)."""
    result = await db.execute(
        select(BookSection).where(BookSection.id == section_id, BookSection.book_id == book_id)
    )
    section = result.scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    await db.delete(section)
    await db.commit()
    return {"status": "ok"}


# ─── Step 5: Extract Recipes ────────────────────────────────────────

@router.post("/{book_id}/extract-recipes")
async def extract_recipes(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """LLM-based recipe extraction from identified recipe_block sections."""
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text available")

    # Get recipe_block sections
    result = await db.execute(
        select(BookSection).where(
            BookSection.book_id == book_id,
            BookSection.section_type == "recipe_block",
        ).order_by(BookSection.start_line)
    )
    sections = result.scalars().all()
    if not sections:
        raise HTTPException(status_code=400, detail="No recipe sections found. Run analyze first.")

    lines = book.full_text.split("\n")

    # Delete existing recipes
    await db.execute(delete(Recipe).where(Recipe.book_id == book_id))
    await db.flush()

    all_recipes = []

    for section in sections:
        # Extract section text
        section_lines = lines[max(0, section.start_line - 1):min(section.end_line, len(lines))]
        section_text = "\n".join(section_lines)

        extracted = await extract_recipes_from_section(
            section_text,
            book_title=book.title,
            recipe_pattern=section.recipe_pattern,
        )

        for er in extracted:
            recipe = Recipe(
                book_id=book_id,
                name=er.name,
                category=er.category,
                original_text=er.original_text,
                qdrant_collection="recipes_v2",
            )
            db.add(recipe)
            await db.flush()

            for ing in er.ingredients:
                db.add(RecipeIngredient(
                    recipe_id=recipe.id,
                    name=ing.name,
                    original_name=ing.original,
                    amount=ing.amount,
                    unit=ing.unit,
                ))

            all_recipes.append({
                "id": str(recipe.id),
                "name": er.name,
                "category": er.category,
                "ingredients_count": len(er.ingredients),
                "text_preview": er.original_text[:150] + "..." if len(er.original_text) > 150 else er.original_text,
            })

    book.wizard_step = 6
    book.status = "recipes_extracted"
    log = ProcessingLog(book_id=book_id, step="extract_recipes", status="completed",
                        details={"recipes_count": len(all_recipes),
                                 "sections_processed": len(sections)})
    db.add(log)
    await db.commit()

    return {"status": "ok", "recipes_count": len(all_recipes), "recipes": all_recipes}


class RecipeUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    original_text: str | None = None


@router.put("/{book_id}/recipes/{recipe_id}")
async def update_recipe(
    book_id: uuid.UUID,
    recipe_id: uuid.UUID,
    update: RecipeUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Edit an extracted recipe."""
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.book_id == book_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if update.name is not None:
        recipe.name = update.name
    if update.category is not None:
        recipe.category = update.category
    if update.original_text is not None:
        recipe.original_text = update.original_text

    await db.commit()
    return {"status": "ok"}


@router.delete("/{book_id}/recipes/{recipe_id}")
async def delete_recipe(
    book_id: uuid.UUID,
    recipe_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a recipe."""
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.book_id == book_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    await db.delete(recipe)
    await db.commit()
    return {"status": "ok"}


# ─── Step 6: Match Ingredients ───────────────────────────────────────

@router.post("/{book_id}/match-ingredients")
async def match_ingredients(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Match extracted ingredients against the global dictionary.

    For each ingredient in the book's recipes:
    - If exact match in dictionary → link it
    - If synonym match → link it
    - If no match → mark as new, create entry
    """
    # Get all recipes with ingredients
    result = await db.execute(
        select(Recipe).where(Recipe.book_id == book_id)
    )
    recipes = result.scalars().all()
    if not recipes:
        raise HTTPException(status_code=400, detail="No recipes found")

    # Load existing ingredients and synonyms
    all_ingredients = (await db.execute(select(Ingredient))).scalars().all()
    all_synonyms = (await db.execute(select(IngredientSynonym))).scalars().all()

    # Build lookup maps
    name_to_ingredient = {i.canonical_name.lower(): i for i in all_ingredients}
    synonym_to_ingredient = {s.synonym.lower(): s.ingredient_id for s in all_synonyms}

    matched = 0
    new_created = 0
    results = []

    for recipe in recipes:
        # Reload ingredients for this recipe
        ing_result = await db.execute(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
        )
        recipe_ingredients = ing_result.scalars().all()

        for ri in recipe_ingredients:
            name_lower = ri.name.lower().strip()
            if not name_lower:
                continue

            # Try exact match
            if name_lower in name_to_ingredient:
                ri.ingredient_id = name_to_ingredient[name_lower].id
                matched += 1
                results.append({"name": ri.name, "status": "matched", "canonical": name_lower})
                continue

            # Try synonym match
            if name_lower in synonym_to_ingredient:
                ri.ingredient_id = synonym_to_ingredient[name_lower]
                matched += 1
                results.append({"name": ri.name, "status": "synonym_match"})
                continue

            # Create new ingredient
            new_ing = Ingredient(canonical_name=ri.name.strip(), category="other")
            db.add(new_ing)
            await db.flush()

            ri.ingredient_id = new_ing.id
            name_to_ingredient[name_lower] = new_ing
            new_created += 1
            results.append({"name": ri.name, "status": "new"})

    book = await _get_book(book_id, db)
    book.wizard_step = 7
    log = ProcessingLog(book_id=book_id, step="match_ingredients", status="completed",
                        details={"matched": matched, "new_created": new_created})
    db.add(log)
    await db.commit()

    return {
        "status": "ok",
        "matched": matched,
        "new_created": new_created,
        "total": matched + new_created,
        "ingredients": results,
    }


# ─── Step 7: Index to Qdrant ────────────────────────────────────────

QDRANT_COLLECTION = "recipes_v2"


@router.post("/{book_id}/index")
async def index_to_qdrant(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Vectorize recipes and index to Qdrant.

    Creates BGE-M3 embeddings (dense + sparse) and upserts to recipes_v2 collection.
    """
    book = await _get_book(book_id, db)

    # Ensure collection exists
    await qdrant_svc.ensure_collection(QDRANT_COLLECTION)

    # Delete existing points for this book
    await qdrant_svc.delete_by_filter(QDRANT_COLLECTION, "book_id", str(book_id))

    # Get all recipes
    result = await db.execute(select(Recipe).where(Recipe.book_id == book_id))
    recipes = result.scalars().all()
    if not recipes:
        raise HTTPException(status_code=400, detail="No recipes to index")

    points = []
    for recipe in recipes:
        # Get ingredients for payload
        ing_result = await db.execute(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
        )
        ingredients = [ri.name for ri in ing_result.scalars().all()]

        # Build text for embedding
        embed_text = f"Рецепт: {recipe.name}\n\nСодержание: {recipe.original_text or ''}"

        # Generate embeddings
        embedding = await create_embedding(embed_text)

        point = {
            "id": str(recipe.id),
            "dense": embedding["dense"],
            "payload": {
                "recipe_name": recipe.name,
                "category": recipe.category or "",
                "source_book": book.title,
                "book_id": str(book_id),
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

        # Update recipe with Qdrant info
        recipe.qdrant_point_id = str(recipe.id)
        recipe.qdrant_collection = QDRANT_COLLECTION
        from datetime import datetime, timezone
        recipe.indexed_at = datetime.now(timezone.utc)

    # Batch upsert (groups of 50)
    for i in range(0, len(points), 50):
        batch = points[i:i + 50]
        await qdrant_svc.upsert_points(QDRANT_COLLECTION, batch)

    book.wizard_step = 8
    book.status = "indexed"
    log = ProcessingLog(book_id=book_id, step="index", status="completed",
                        details={"points_indexed": len(points), "collection": QDRANT_COLLECTION})
    db.add(log)
    await db.commit()

    return {
        "status": "ok",
        "points_indexed": len(points),
        "collection": QDRANT_COLLECTION,
    }


# ─── Status ─────────────────────────────────────────────────────────

@router.get("/{book_id}/status")
async def wizard_status(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get current wizard state for a book."""
    book = await _get_book(book_id, db)

    # Count entities
    pages_count = (await db.execute(
        select(BookPage).where(BookPage.book_id == book_id)
    )).scalars().all()

    sections_count = (await db.execute(
        select(BookSection).where(BookSection.book_id == book_id)
    )).scalars().all()

    recipes_count = (await db.execute(
        select(Recipe).where(Recipe.book_id == book_id)
    )).scalars().all()

    # Get processing logs
    logs_result = await db.execute(
        select(ProcessingLog).where(ProcessingLog.book_id == book_id)
        .order_by(ProcessingLog.created_at.desc())
    )
    logs = logs_result.scalars().all()

    return {
        "book_id": str(book_id),
        "title": book.title,
        "wizard_step": book.wizard_step or 1,
        "status": book.status,
        "pdf_type": book.pdf_type,
        "language": book.language,
        "has_full_text": bool(book.full_text),
        "text_length": len(book.full_text) if book.full_text else 0,
        "pages_count": len(pages_count),
        "sections_count": len(sections_count),
        "recipes_count": len(recipes_count),
        "logs": [
            {
                "step": log.step,
                "status": log.status,
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs[:20]
        ],
    }
