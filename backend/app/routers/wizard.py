"""Book processing wizard: step-by-step interactive pipeline.

Long-running steps (extract, analyze, extract-recipes, etc.) run as
asyncio background tasks.  The frontend polls GET /progress to see
real-time status updates instead of waiting for the HTTP response.

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

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
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
from app.services.text_transform import transform_text_chunked
from app.services.embedder import create_embedding
from app.services import qdrant as qdrant_svc
from app.services.ingest import BORN_TEXT_FORMATS, extract_text_from_document

logger = logging.getLogger(__name__)
router = APIRouter()

MIN_TEXT_LENGTH = 50


# ──────────────────────────────────────────────────────────────────────
# In-memory progress tracker
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TaskProgress:
    step: str
    status: str = "running"          # running | completed | error | cancelled
    started_at: float = 0.0
    finished_at: float | None = None
    messages: list[str] = field(default_factory=list)
    result: dict | None = None
    error_text: str | None = None
    task: "asyncio.Task | None" = None   # background task handle (for cancellation)

    def log(self, msg: str):
        elapsed = time.time() - self.started_at
        ts = f"[{elapsed:.0f}s]"
        entry = f"{ts} {msg}"
        self.messages.append(entry)
        logger.info(f"Progress({self.step}): {msg}")

    def finish(self, result: dict | None = None):
        self.status = "completed"
        self.finished_at = time.time()
        self.result = result
        self.log("Done")

    def fail(self, err: str):
        self.status = "error"
        self.finished_at = time.time()
        self.error_text = err
        self.log(f"ERROR: {err}")


# book_id -> TaskProgress
_tasks: dict[str, TaskProgress] = {}


def _start_progress(book_id: str, step: str) -> TaskProgress:
    tp = TaskProgress(step=step, started_at=time.time())
    tp.log(f"Starting {step}")
    _tasks[book_id] = tp
    return tp


def _get_progress(book_id: str) -> TaskProgress | None:
    return _tasks.get(book_id)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _get_book(book_id: uuid.UUID, db: AsyncSession) -> Book:
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def _check_not_busy(book_id: str):
    tp = _get_progress(book_id)
    if tp and tp.status == "running":
        raise HTTPException(
            status_code=409,
            detail=f"Step '{tp.step}' is already running for this book",
        )


# ──────────────────────────────────────────────────────────────────────
# Progress endpoint  (polled by frontend every 3s)
# ──────────────────────────────────────────────────────────────────────

@router.get("/{book_id}/progress")
async def get_progress(book_id: uuid.UUID):
    bid = str(book_id)
    tp = _get_progress(bid)
    if not tp:
        return {"running": False, "step": None, "messages": [], "status": "idle"}
    elapsed = (tp.finished_at or time.time()) - tp.started_at
    return {
        "running": tp.status == "running",
        "step": tp.step,
        "status": tp.status,
        "elapsed": round(elapsed, 1),
        "messages": tp.messages[-50:],
        "error": tp.error_text,
        "result": tp.result,
    }


# ──────────────────────────────────────────────────────────────────────
# Cancel a running background step
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/cancel")
async def cancel_task(book_id: uuid.UUID):
    bid = str(book_id)
    tp = _get_progress(bid)
    if not tp or tp.status != "running":
        return {"status": "no_running_task"}

    if tp.task and not tp.task.done():
        tp.task.cancel()

    tp.status = "cancelled"
    tp.finished_at = time.time()
    tp.error_text = "Cancelled by user"
    tp.log("Cancelled by user")
    return {"status": "cancelled", "step": tp.step}


# ──────────────────────────────────────────────────────────────────────
# Step 1: Classify  (fast, synchronous)
# ──────────────────────────────────────────────────────────────────────

def _detect_language(sample_text: str) -> tuple[int, float, str]:
    """Classify Russian orthography from a text sample.

    Pre-reform Russian detection. The obvious markers (ѣ і ѳ) are often
    destroyed by a low-quality OCR layer (ѣ -> Ь, і -> и/н), so we ALSO use the
    most robust signal that survives OCR: the word-final hard sign ъ. In
    pre-reform orthography nearly every consonant-final word ends in ъ
    ("въ заторный чанъ", "пудъ хлѣба"); in modern Russian word-final ъ is
    essentially absent. Returns (yat_char_count, final-hard ratio, language).
    """
    yat_chars = sum(1 for c in sample_text if c in "ѣѢіІѳѲ")
    words = sample_text.split()
    final_hard = sum(
        1 for w in words if w.strip(".,;:!?()[]«»\"'—-–…").endswith(("ъ", "Ъ"))
    )
    hard_ratio = final_hard / max(1, len(words))
    language = "pre_reform_ru" if (yat_chars > 3 or hard_ratio > 0.08) else "modern_ru"
    return yat_chars, hard_ratio, language


@router.post("/{book_id}/classify")
async def classify_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    book = await _get_book(book_id, db)
    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no source file")

    source_format = book.source_format or "pdf"

    if source_format in BORN_TEXT_FORMATS:
        # Born-text (txt/docx): text is already in the file, no OCR/PDF split.
        data = minio_svc.download_file(book.file_path)
        text = extract_text_from_document(data, source_format)
        pdf_type = "text"
        total_pages = 1
        text_pages = 1
        check_pages = [None]
        yat_chars, hard_ratio, language = _detect_language(text[:5000])
        log_details = {"pdf_type": pdf_type, "language": language, "source_format": source_format,
                       "text_length": len(text), "yat_chars": yat_chars,
                       "final_hard_ratio": round(hard_ratio, 3)}
    else:
        # PDF (incl. DjVu already converted to PDF on upload).
        pdf_bytes = minio_svc.download_file(book.file_path)
        pages = split_pdf_smart(pdf_bytes)
        total_pages = len(pages)

        check_pages = pages[1:6] if len(pages) > 1 else pages[:5]
        text_pages = sum(1 for p in check_pages if p["page_type"] == "text")
        pdf_type = "text" if text_pages >= len(check_pages) * 0.5 else "image"

        sample_text = " ".join(p["text"] for p in check_pages if p["text"])
        yat_chars, hard_ratio, language = _detect_language(sample_text)
        log_details = {"pdf_type": pdf_type, "language": language, "source_format": source_format,
                       "pages_checked": len(check_pages), "text_pages": text_pages,
                       "yat_chars": yat_chars, "final_hard_ratio": round(hard_ratio, 3),
                       "total_pages": total_pages}

    book.pdf_type = pdf_type
    book.language = language
    book.wizard_step = 2
    book.status = "classified"

    db.add(ProcessingLog(book_id=book_id, step="classify", status="completed", details=log_details))
    await db.commit()

    return {
        "status": "ok",
        "pdf_type": pdf_type,
        "language": language,
        "total_pages": total_pages,
        "text_pages_in_sample": text_pages,
        "sample_size": len(check_pages),
    }


# ──────────────────────────────────────────────────────────────────────
# Step 2: Extract Text  (background)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/extract")
async def extract_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    book = await _get_book(book_id, db)
    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no PDF file")

    tp = _start_progress(bid, "extract")
    tp.task = asyncio.create_task(
        _bg_extract(book_id, book.file_path, book.pdf_type, book.source_format or "pdf", tp)
    )
    return {"status": "started", "step": "extract"}


async def _bg_extract(book_id: uuid.UUID, file_path: str, pdf_type: str,
                      source_format: str, tp: TaskProgress):
    try:
        # Born-text (txt/docx): the file already holds text — no PDF split, no OCR.
        if source_format in BORN_TEXT_FORMATS:
            tp.log(f"Reading born-text document ({source_format})")
            data = minio_svc.download_file(file_path)
            full_text = extract_text_from_document(data, source_format)
            async with async_session() as db:
                existing = await db.execute(select(BookPage).where(BookPage.book_id == book_id))
                for p in existing.scalars().all():
                    await db.delete(p)
                await db.flush()
                db.add(BookPage(
                    book_id=book_id, page_number=1, image_path=None, raw_text=full_text,
                    dpi=0, ocr_confidence=100.0, needs_review=False, status="text_extracted",
                ))
                book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
                book.full_text = full_text
                book.wizard_step = 3
                book.status = "extracted"
                db.add(ProcessingLog(book_id=book_id, step="extract", status="completed",
                                     details={"total_pages": 1, "text_length": len(full_text),
                                              "source_format": source_format}))
                await db.commit()
            tp.finish({"total_pages": 1, "text_length": len(full_text)})
            return

        tp.log("Downloading PDF from storage")
        pdf_bytes = minio_svc.download_file(file_path)
        tp.log("Splitting PDF into pages")
        pages = split_pdf_smart(pdf_bytes)
        tp.log(f"Found {len(pages)} pages")

        async with async_session() as db:
            # Delete existing pages
            existing = await db.execute(select(BookPage).where(BookPage.book_id == book_id))
            for p in existing.scalars().all():
                await db.delete(p)
            await db.flush()

            all_texts = []
            for i, page_data in enumerate(pages):
                page_num = page_data["page_number"]
                page_type = page_data["page_type"]
                tp.log(f"Page {page_num}/{len(pages)} ({page_type})")

                image_path = None
                raw_text = page_data["text"]
                confidence = 100.0 if page_type == "text" else None
                method = "pdf_extract" if page_type == "text" else None
                status = "text_extracted" if page_type == "text" else "needs_ocr"

                if page_type == "image" and page_data["image_bytes"]:
                    image_path = f"books/{book_id}/pages/{page_num:04d}.png"
                    minio_svc.upload_file(page_data["image_bytes"], image_path, content_type="image/png")
                    text, conf, ocr_method = await ocr_page_with_fallback(page_data["image_bytes"])
                    raw_text = text
                    confidence = conf
                    method = ocr_method
                    status = "ocr_done"

                page = BookPage(
                    book_id=book_id, page_number=page_num, image_path=image_path,
                    raw_text=raw_text, dpi=page_data["dpi"], ocr_confidence=confidence,
                    needs_review=confidence is not None and confidence < 60.0, status=status,
                )
                db.add(page)
                if raw_text:
                    all_texts.append(raw_text)

            full_text = "\n\n".join(all_texts)
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.full_text = full_text
            book.wizard_step = 3
            book.status = "extracted"

            log = ProcessingLog(book_id=book_id, step="extract", status="completed",
                                details={"total_pages": len(pages), "text_length": len(full_text)})
            db.add(log)
            await db.commit()

            tp.finish({"total_pages": len(pages), "text_length": len(full_text)})
    except Exception as e:
        logger.exception("Extract failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Step 2b: Cleanup  (background)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/cleanup")
async def cleanup_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to clean up. Run extract first.")

    tp = _start_progress(bid, "cleanup")
    tp.task = asyncio.create_task(
        _bg_cleanup(book_id, book.full_text, book.pdf_type, book.language, tp)
    )
    return {"status": "started", "step": "cleanup"}


CLEANUP_SYSTEM_PROMPT = (
    "You are cleaning up OCR text from a historical Russian book. "
    "Fix obvious OCR errors, broken words, and artifacts while preserving "
    "the original meaning and style. Keep the original orthography if present "
    "(do NOT modernize spelling — that is a later step). "
    "Return the cleaned text only, no comments."
)


async def _bg_cleanup(
    book_id: uuid.UUID, full_text: str, pdf_type: str, language: str, tp: TaskProgress
):
    try:
        tp.log("Applying rule-based cleanup")
        cleaned = clean_ocr_text(full_text)

        # LLM OCR-artifact cleanup pays off for image PDFs (raw OCR) and for
        # pre-reform books (whose embedded text layer is bad third-party OCR).
        use_llm = pdf_type == "image" or language == "pre_reform_ru"
        if use_llm:
            tp.log(f"Sending to LLM for OCR artifact cleanup (chunked, {len(cleaned)} chars)")
            cleaned = await transform_text_chunked(
                cleaned,
                system_prompt=CLEANUP_SYSTEM_PROMPT,
                task="text_cleanup",
                cb=tp.log,
            )
            tp.log("LLM cleanup complete")

        async with async_session() as db:
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.full_text = cleaned
            log = ProcessingLog(book_id=book_id, step="cleanup", status="completed",
                                details={"text_length": len(cleaned), "used_llm": use_llm})
            db.add(log)
            await db.commit()

        tp.finish({"text_length": len(cleaned), "used_llm": use_llm})
    except Exception as e:
        logger.exception("Cleanup failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Step 3: Translate  (background)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/translate")
async def translate_text(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to translate. Run extract first.")

    if book.language != "pre_reform_ru":
        book.wizard_step = 4
        await db.commit()
        return {"status": "skipped", "reason": "Book is modern Russian, no translation needed"}

    bid = str(book_id)
    _check_not_busy(bid)
    tp = _start_progress(bid, "translate")
    tp.task = asyncio.create_task(_bg_translate(book_id, book.full_text, tp))
    return {"status": "started", "step": "translate"}


TRANSLATE_SYSTEM_PROMPT = (
    "You are translating a historical Russian text from pre-reform orthography "
    "and archaic language to modern Russian. The text is about herbal tinctures, "
    "distillates, and medicinal preparations. "
    "Modernize archaic words and constructions while preserving the exact meaning. "
    "Keep recipe names, ingredient names, and measurement units recognizable. "
    "Return only the translated text."
)


async def _bg_translate(book_id: uuid.UUID, full_text: str, tp: TaskProgress):
    try:
        tp.log("Applying rule-based orthographic normalization")
        normalized = normalize_orthography(full_text)
        tp.log(f"Normalized: {len(normalized)} chars")

        tp.log(f"Sending to LLM for archaic language translation (chunked, {len(normalized)} chars)")
        translated = await transform_text_chunked(
            normalized,
            system_prompt=TRANSLATE_SYSTEM_PROMPT,
            task="translation",
            cb=tp.log,
        )
        tp.log(f"Translated: {len(translated)} chars")

        async with async_session() as db:
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.full_text = translated
            book.wizard_step = 4
            log = ProcessingLog(book_id=book_id, step="translate", status="completed",
                                details={"original_length": len(normalized), "translated_length": len(translated)})
            db.add(log)
            await db.commit()

        tp.finish({"original_length": len(normalized), "translated_length": len(translated)})
    except Exception as e:
        logger.exception("Translate failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Step 4: Analyze Structure  (background, with progress callback)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/analyze")
async def analyze_structure(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text to analyze. Run extract first.")

    tp = _start_progress(bid, "analyze")
    tp.task = asyncio.create_task(_bg_analyze(book_id, book.full_text, book.title, book.year, tp))
    return {"status": "started", "step": "analyze"}


async def _bg_analyze(book_id: uuid.UUID, full_text: str, title: str, year: int | None, tp: TaskProgress):
    try:
        tp.log(f"Text: {len(full_text)} chars, {full_text.count(chr(10))+1} lines")
        sections = await analyze_book_structure(
            full_text, book_title=title, book_year=year,
            progress_callback=tp.log,
        )
        tp.log(f"Found {len(sections)} sections")

        async with async_session() as db:
            await db.execute(delete(BookSection).where(BookSection.book_id == book_id))
            await db.flush()

            lines = full_text.split("\n")
            section_results = []

            for s in sections:
                start_char = sum(len(lines[i]) + 1 for i in range(min(s.start_line - 1, len(lines))))
                end_char = sum(len(lines[i]) + 1 for i in range(min(s.end_line, len(lines))))
                section_text = "\n".join(lines[max(0, s.start_line - 1):min(s.end_line, len(lines))])
                preview = section_text[:200] + "..." if len(section_text) > 200 else section_text

                db_section = BookSection(
                    book_id=book_id, section_type=s.section_type, title=s.title,
                    start_line=s.start_line, end_line=s.end_line,
                    start_char=start_char, end_char=end_char,
                    content_preview=preview, recipe_pattern=s.recipe_pattern,
                    estimated_recipe_count=s.estimated_recipe_count, confidence=s.confidence,
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

            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.wizard_step = 5
            book.status = "analyzed"
            log = ProcessingLog(book_id=book_id, step="analyze", status="completed",
                                details={"sections_found": len(sections),
                                         "recipe_blocks": sum(1 for s in sections if s.section_type == "recipe_block")})
            db.add(log)
            await db.commit()

        tp.finish({"sections": section_results})
    except Exception as e:
        logger.exception("Analyze failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Section CRUD (synchronous, lightweight)
# ──────────────────────────────────────────────────────────────────────

class SectionUpdate(BaseModel):
    section_type: str
    title: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@router.put("/{book_id}/sections/{section_id}")
async def update_section(
    book_id: uuid.UUID, section_id: uuid.UUID,
    update: SectionUpdate, db: AsyncSession = Depends(get_db),
):
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
    book_id: uuid.UUID, section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BookSection).where(BookSection.id == section_id, BookSection.book_id == book_id)
    )
    section = result.scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    await db.delete(section)
    await db.commit()
    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────
# Step 5: Extract Recipes  (background, with progress callback)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/extract-recipes")
async def extract_recipes(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    book = await _get_book(book_id, db)
    if not book.full_text:
        raise HTTPException(status_code=400, detail="No text available")

    result = await db.execute(
        select(BookSection).where(
            BookSection.book_id == book_id,
            BookSection.section_type == "recipe_block",
        ).order_by(BookSection.start_line)
    )
    sections = result.scalars().all()
    if not sections:
        raise HTTPException(status_code=400, detail="No recipe sections found. Run analyze first.")

    # Prepare section data for background task
    lines = book.full_text.split("\n")
    section_data = []
    for s in sections:
        section_lines = lines[max(0, s.start_line - 1):min(s.end_line, len(lines))]
        section_data.append({
            "text": "\n".join(section_lines),
            "title": s.title,
            "recipe_pattern": s.recipe_pattern,
        })

    tp = _start_progress(bid, "extract-recipes")
    tp.task = asyncio.create_task(_bg_extract_recipes(book_id, book.title, section_data, tp))
    return {"status": "started", "step": "extract-recipes"}


async def _bg_extract_recipes(
    book_id: uuid.UUID, book_title: str,
    section_data: list[dict], tp: TaskProgress,
):
    try:
        tp.log(f"Processing {len(section_data)} recipe sections")

        async with async_session() as db:
            await db.execute(delete(Recipe).where(Recipe.book_id == book_id))
            await db.flush()

            all_recipes = []

            failed_sections = 0
            for i, sd in enumerate(section_data):
                tp.log(f"Section {i+1}/{len(section_data)}: {sd['title']} ({len(sd['text'])} chars)")

                try:
                    extracted = await extract_recipes_from_section(
                        sd["text"],
                        book_title=book_title,
                        recipe_pattern=sd["recipe_pattern"],
                        progress_callback=tp.log,
                    )
                except Exception as e:
                    # One bad section (truncated JSON, transient LLM error) must
                    # not abort the whole extraction — log, skip, keep the rest.
                    failed_sections += 1
                    logger.exception(f"Section {i+1} extraction failed")
                    tp.log(f"Section {i+1}: ERROR - {e} (skipped)")
                    continue

                tp.log(f"Section {i+1}: extracted {len(extracted)} recipes")

                for er in extracted:
                    recipe = Recipe(
                        book_id=book_id, name=er.name, category=er.category,
                        original_text=er.original_text, qdrant_collection="recipes_v2",
                    )
                    db.add(recipe)
                    await db.flush()

                    for ing in er.ingredients:
                        db.add(RecipeIngredient(
                            recipe_id=recipe.id, name=ing.name,
                            original_name=ing.original, amount=ing.amount, unit=ing.unit,
                        ))

                    all_recipes.append({
                        "id": str(recipe.id),
                        "name": er.name,
                        "category": er.category,
                        "ingredients_count": len(er.ingredients),
                        "text_preview": er.original_text[:150] + "..." if len(er.original_text) > 150 else er.original_text,
                    })

            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.wizard_step = 6
            book.status = "recipes_extracted"
            log = ProcessingLog(book_id=book_id, step="extract_recipes", status="completed",
                                details={"recipes_count": len(all_recipes),
                                         "sections_processed": len(section_data),
                                         "sections_failed": failed_sections})
            db.add(log)
            await db.commit()

        tp.finish({"recipes_count": len(all_recipes), "recipes": all_recipes})
    except Exception as e:
        logger.exception("Extract recipes failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Recipe CRUD (synchronous)
# ──────────────────────────────────────────────────────────────────────

class RecipeUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    original_text: str | None = None


@router.put("/{book_id}/recipes/{recipe_id}")
async def update_recipe(
    book_id: uuid.UUID, recipe_id: uuid.UUID,
    update: RecipeUpdate, db: AsyncSession = Depends(get_db),
):
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
    book_id: uuid.UUID, recipe_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.book_id == book_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    await db.delete(recipe)
    await db.commit()
    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────────
# Step 6: Match Ingredients  (background)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{book_id}/match-ingredients")
async def match_ingredients(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    await _get_book(book_id, db)

    tp = _start_progress(bid, "match-ingredients")
    tp.task = asyncio.create_task(_bg_match_ingredients(book_id, tp))
    return {"status": "started", "step": "match-ingredients"}


async def _bg_match_ingredients(book_id: uuid.UUID, tp: TaskProgress):
    try:
        async with async_session() as db:
            result = await db.execute(select(Recipe).where(Recipe.book_id == book_id))
            recipes = result.scalars().all()
            if not recipes:
                tp.fail("No recipes found")
                return

            tp.log(f"Matching ingredients for {len(recipes)} recipes")

            all_ingredients = (await db.execute(select(Ingredient))).scalars().all()
            all_synonyms = (await db.execute(select(IngredientSynonym))).scalars().all()

            name_to_ingredient = {i.canonical_name.lower(): i for i in all_ingredients}
            synonym_to_ingredient = {s.synonym.lower(): s.ingredient_id for s in all_synonyms}

            matched = 0
            new_created = 0

            for ri_idx, recipe in enumerate(recipes):
                ing_result = await db.execute(
                    select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
                )
                recipe_ingredients = ing_result.scalars().all()

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
                    tp.log(f"Recipe {ri_idx+1}/{len(recipes)}: matched={matched}, new={new_created}")

            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()
            book.wizard_step = 7
            log = ProcessingLog(book_id=book_id, step="match_ingredients", status="completed",
                                details={"matched": matched, "new_created": new_created})
            db.add(log)
            await db.commit()

        tp.finish({"matched": matched, "new_created": new_created, "total": matched + new_created})
    except Exception as e:
        logger.exception("Match ingredients failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Step 7: Index to Qdrant  (background)
# ──────────────────────────────────────────────────────────────────────

QDRANT_COLLECTION = "recipes_v2"


@router.post("/{book_id}/index")
async def index_to_qdrant(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bid = str(book_id)
    _check_not_busy(bid)
    await _get_book(book_id, db)

    tp = _start_progress(bid, "index")
    tp.task = asyncio.create_task(_bg_index(book_id, tp))
    return {"status": "started", "step": "index"}


async def _bg_index(book_id: uuid.UUID, tp: TaskProgress):
    try:
        async with async_session() as db:
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one()

            tp.log("Ensuring Qdrant collection exists")
            await qdrant_svc.ensure_collection(QDRANT_COLLECTION)
            await qdrant_svc.delete_by_filter(QDRANT_COLLECTION, "book_id", str(book_id))

            result = await db.execute(select(Recipe).where(Recipe.book_id == book_id))
            recipes = result.scalars().all()
            if not recipes:
                tp.fail("No recipes to index")
                return

            tp.log(f"Indexing {len(recipes)} recipes")
            points = []

            for i, recipe in enumerate(recipes):
                ing_result = await db.execute(
                    select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
                )
                ingredients = [ri.name for ri in ing_result.scalars().all()]

                embed_text = f"Рецепт: {recipe.name}\n\nСодержание: {recipe.original_text or ''}"
                tp.log(f"Embedding {i+1}/{len(recipes)}: {recipe.name}")
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

                recipe.qdrant_point_id = str(recipe.id)
                recipe.qdrant_collection = QDRANT_COLLECTION
                recipe.indexed_at = datetime.now(timezone.utc)

            tp.log("Upserting points to Qdrant")
            for i in range(0, len(points), 50):
                batch = points[i:i + 50]
                await qdrant_svc.upsert_points(QDRANT_COLLECTION, batch)
                tp.log(f"Upserted batch {i//50 + 1}")

            book.wizard_step = 8
            book.status = "indexed"
            log = ProcessingLog(book_id=book_id, step="index", status="completed",
                                details={"points_indexed": len(points), "collection": QDRANT_COLLECTION})
            db.add(log)
            await db.commit()

        tp.finish({"points_indexed": len(points), "collection": QDRANT_COLLECTION})
    except Exception as e:
        logger.exception("Index failed")
        tp.fail(str(e))


# ──────────────────────────────────────────────────────────────────────
# Temporal: run the WHOLE pipeline as one durable workflow
# ──────────────────────────────────────────────────────────────────────
#
# This is the durable replacement for chaining the individual /classify,
# /extract, ... endpoints from the frontend.  One workflow runs every step
# end-to-end; it survives a backend/worker restart and retries transient LLM
# and HTTP failures.  Imports are local so the rest of the wizard (and the test
# suite) doesn't hard-depend on the temporalio package being installed.

def _workflow_id(book_id: uuid.UUID) -> str:
    return f"book-pipeline-{book_id}"


@router.post("/{book_id}/run")
async def run_pipeline(
    book_id: uuid.UUID,
    start_step: str = "classify",
    db: AsyncSession = Depends(get_db),
):
    """Start the end-to-end durable pipeline workflow for a book.

    ``start_step`` (query param) optionally resumes the pipeline partway
    through — useful when earlier steps are already committed and re-running
    them would waste expensive LLM work.
    """
    await _get_book(book_id, db)  # 404 if missing

    from temporalio.service import RPCError
    from app.config import settings
    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import BookPipelineWorkflow, STEP_NAMES

    if start_step not in STEP_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid start_step '{start_step}'. Must be one of: {', '.join(STEP_NAMES)}",
        )

    client = await get_temporal_client()
    wf_id = _workflow_id(book_id)

    # Reject if one is already running for this book.
    try:
        desc = await client.get_workflow_handle(wf_id).describe()
        if desc.status and desc.status.name == "RUNNING":
            raise HTTPException(status_code=409, detail="Pipeline already running for this book")
    except RPCError:
        pass  # no existing workflow — fine

    handle = await client.start_workflow(
        BookPipelineWorkflow.run,
        args=[str(book_id), start_step],
        id=wf_id,
        task_queue=settings.temporal_task_queue,
    )
    return {"status": "started", "workflow_id": handle.id,
            "run_id": handle.result_run_id, "start_step": start_step}


@router.get("/{book_id}/workflow")
async def workflow_status(book_id: uuid.UUID):
    """Describe the durable pipeline workflow.

    Surfaces enough for a live UI: overall status, the currently-running step,
    that step's latest heartbeat detail (e.g. "Chunk 14/81"), the retry attempt,
    the list of completed steps (to drive the step indicator), and the final
    per-step result once the workflow finishes.
    """
    from temporalio.service import RPCError
    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import STEP_NAMES

    client = await get_temporal_client()
    handle = client.get_workflow_handle(_workflow_id(book_id))
    try:
        desc = await handle.describe()
    except RPCError:
        return {"exists": False, "status": "none"}

    status = desc.status.name if desc.status else "UNKNOWN"

    # Inspect the running activity (if any) from the raw describe response.
    current_step = None
    current_detail = None
    current_attempt = None
    try:
        pending = list(getattr(desc.raw_description, "pending_activities", []))
        if pending:
            pa = pending[0]
            current_step = pa.activity_type.name.replace("_activity", "")
            current_attempt = pa.attempt
            hb = getattr(pa, "heartbeat_details", None)
            if hb and getattr(hb, "payloads", None):
                decoded = await client.data_converter.decode(list(hb.payloads))
                if decoded:
                    current_detail = decoded[0]
    except Exception:  # pragma: no cover - defensive against SDK shape changes
        pass

    result = None
    if status == "COMPLETED":
        try:
            result = await handle.result()
        except Exception as e:  # pragma: no cover - defensive
            result = {"error": str(e)}

    # Derive completed steps for the UI indicator.  The workflow runs strictly
    # in order, so everything before the running step is done; once finished,
    # everything is done.
    if status == "COMPLETED":
        completed_steps = list(STEP_NAMES)
    elif current_step in STEP_NAMES:
        completed_steps = STEP_NAMES[: STEP_NAMES.index(current_step)]
    else:
        completed_steps = []

    return {
        "exists": True,
        "workflow_id": handle.id,
        "status": status,
        "start_time": desc.start_time.isoformat() if desc.start_time else None,
        "close_time": desc.close_time.isoformat() if desc.close_time else None,
        "current_step": current_step,
        "current_detail": current_detail,
        "current_attempt": current_attempt,
        "completed_steps": completed_steps,
        "result": result,
    }


@router.post("/{book_id}/workflow/cancel")
async def workflow_cancel(book_id: uuid.UUID):
    """Cancel a running pipeline workflow."""
    from temporalio.service import RPCError
    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    handle = client.get_workflow_handle(_workflow_id(book_id))
    try:
        await handle.cancel()
    except RPCError as e:
        raise HTTPException(status_code=404, detail=f"No workflow to cancel: {e}")
    return {"status": "cancel_requested"}


# ──────────────────────────────────────────────────────────────────────
# Status
# ──────────────────────────────────────────────────────────────────────

@router.get("/{book_id}/status")
async def wizard_status(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    book = await _get_book(book_id, db)

    pages_count = len((await db.execute(
        select(BookPage).where(BookPage.book_id == book_id)
    )).scalars().all())

    sections_count = len((await db.execute(
        select(BookSection).where(BookSection.book_id == book_id)
    )).scalars().all())

    recipes_count = len((await db.execute(
        select(Recipe).where(Recipe.book_id == book_id)
    )).scalars().all())

    logs_result = await db.execute(
        select(ProcessingLog).where(ProcessingLog.book_id == book_id)
        .order_by(ProcessingLog.created_at.desc())
    )
    logs = logs_result.scalars().all()

    # Include current progress if running
    tp = _get_progress(str(book_id))
    progress = None
    if tp:
        elapsed = (tp.finished_at or time.time()) - tp.started_at
        progress = {
            "running": tp.status == "running",
            "step": tp.step,
            "status": tp.status,
            "elapsed": round(elapsed, 1),
            "messages": tp.messages[-30:],
            "error": tp.error_text,
        }

    return {
        "book_id": str(book_id),
        "title": book.title,
        "wizard_step": book.wizard_step or 1,
        "status": book.status,
        "pdf_type": book.pdf_type,
        "language": book.language,
        "has_full_text": bool(book.full_text),
        "text_length": len(book.full_text) if book.full_text else 0,
        "pages_count": pages_count,
        "sections_count": sections_count,
        "recipes_count": recipes_count,
        "progress": progress,
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
