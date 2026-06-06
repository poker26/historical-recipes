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

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select, delete, func
from temporalio import activity

from app.database import async_session
from app.models.book import Book, BookPage, BookSection, ProcessingLog
from app.models.recipe import Recipe, RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.models.plant import (
    Plant, MedicinalAction, PlantMedicinalUse, PlantCompound,
    PlantHarvest, PlantHabitat, PlantToxicity, PlantBookMention, Compound,
    PlantCulinaryUse,
)
from app.services import minio as minio_svc
from app.services.ingest import BORN_TEXT_FORMATS, extract_text_from_document
from app.services.preprocessor import split_pdf_smart
from app.services.ocr import ocr_page_with_fallback
from app.services.postprocessor import clean_ocr_text
from app.services.normalizer import normalize_orthography
from app.services.structure_analyzer import analyze_book_structure
from app.services.recipe_extractor import extract_recipes_from_section
from app.services.plant_extractor import (
    extract_plants_from_text,
    _split_into_chunks,
    _extract_single,
    _with_heartbeat,
)
from app.services.text_transform import transform_text_chunked
from app.services.embedder import create_embedding
from app.services import qdrant as qdrant_svc
from app.services.plant_matching import (
    PlantMatcher,
    relink_recipe_ingredients,
    same_plant_identity,
    is_more_specific,
    _latin_key,
)
from app.services.compound_extractor import (
    extract_compounds_from_text,
    _split_into_chunks as _split_compound_chunks,
    _extract_single as _extract_compound_single,
    _with_heartbeat as _compound_with_heartbeat,
)
from app.services.compound_matching import normalize_plant_compounds

logger = logging.getLogger(__name__)

QDRANT_COLLECTION = "recipes_v2"
QDRANT_PLANTS_COLLECTION = "plants_v2"
QDRANT_SECTIONS_COLLECTION = "sections_v1"

# Section types worth embedding as free-text passages. We deliberately KEEP
# recipe_block (its prose / inter-recipe connective text is useful even though
# the recipes themselves are also indexed separately — duplication is fine).
# toc / bibliography / chapter_header are pure navigation/noise — skip them.
_INDEXABLE_SECTION_TYPES = {"recipe_block", "introduction", "appendix", "other"}

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
# Step 0: Convert (DjVu → PDF)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def convert_activity(book_id: str) -> dict:
    """Normalize a DjVu upload into a compact PDF, off the upload request path.

    Uploads now store the raw ``.djvu`` (instant), so the heavy ddjvu pass lives
    here as a durable, retriable step: a slow/failed conversion leaves the book
    visibly ``uploaded`` instead of erroring the upload.  After it runs the book's
    ``file_path`` points at the produced ``original.pdf`` and every downstream
    step treats it as an ordinary PDF (``source_format`` stays ``djvu`` for
    provenance — it's not a born-text format, so it routes through the OCR path).

    No-op for non-DjVu sources and for a DjVu already converted (idempotent), so
    it's safe as the universal first step and safe to re-run.
    """
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        source_format = (book.source_format or "pdf").lower()
        file_path = book.file_path

    if source_format != "djvu":
        _hb(f"convert: skip — source_format={source_format}")
        return {"skipped": True, "reason": f"source_format={source_format}"}
    if not file_path:
        raise ValueError("Book has no source file")
    if file_path.endswith(".pdf"):
        _hb("convert: skip — DjVu already converted to PDF")
        return {"skipped": True, "reason": "already_pdf", "pdf_path": file_path}

    from app.services.ingest import djvu_to_pdf

    _hb("convert: downloading DjVu from storage")
    djvu_bytes = minio_svc.download_file(file_path)
    _hb(f"convert: ddjvu DjVu→PDF ({len(djvu_bytes):,} bytes)")
    # ddjvu is a long, CPU-bound blocking subprocess — run it off the event loop
    # so it can't starve other concurrent activities on this worker.
    loop = asyncio.get_event_loop()
    pdf_bytes = await loop.run_in_executor(None, djvu_to_pdf, djvu_bytes)

    pdf_path = f"books/{bid}/original.pdf"
    _hb(f"convert: uploading PDF ({len(pdf_bytes):,} bytes)")
    minio_svc.upload_file(pdf_bytes, pdf_path, content_type="application/pdf")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.file_path = pdf_path
        if not book.pdf_type or book.pdf_type == "pending":
            book.pdf_type = "image"  # scanned DjVu → image PDF; classify refines this
        db.add(ProcessingLog(
            book_id=bid, step="convert", status="completed",
            details={"djvu_bytes": len(djvu_bytes), "pdf_bytes": len(pdf_bytes),
                     "pdf_path": pdf_path},
        ))
        await db.commit()

    _hb(f"convert: DjVu {len(djvu_bytes):,} → PDF {len(pdf_bytes):,} bytes")
    return {"djvu_bytes": len(djvu_bytes), "pdf_bytes": len(pdf_bytes), "pdf_path": pdf_path}


# ──────────────────────────────────────────────────────────────────────
# iNaturalist enrichment (pipeline tail step + standalone corpus sweep)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def enrich_inat_activity(book_id: str | None = None, limit: int = 150,
                               force: bool = False) -> dict:
    """Durable iNaturalist enrichment. Two callers:

    * as the tail step of the herbalism/fungi pipeline it receives the book_id
      and enriches only that book's freshly-added (latin-named, unsynced) plants;
    * ``InatEnrichmentWorkflow`` calls it with ``book_id=None`` to drain the whole
      corpus in paced batches.

    Idempotent (only unsynced plants) and resumable. iNat 429s are absorbed by
    the service's backoff; any unexpected error is swallowed into the summary so
    a transient iNat outage never fails an otherwise-successful book pipeline —
    the plants simply stay unsynced for a later sweep.
    """
    from app.services.inaturalist import enrich_plants_inat

    bid = uuid.UUID(book_id) if book_id else None

    def _progress(done, total, name):
        _hb(f"iNat enrich {done}/{total}: {name}")

    try:
        async with async_session() as db:
            summary = await enrich_plants_inat(
                db, dry_run=False, limit=limit, force=force,
                book_id=bid, progress=_progress,
            )
    except Exception as e:  # noqa: BLE001 — best-effort enrichment, never fatal
        logger.warning(f"iNat enrich activity error: {type(e).__name__}: {e}")
        return {"error": f"{type(e).__name__}: {e}", "processed": 0,
                "taxa_resolved": 0, "photos_set": 0, "no_match": 0,
                "throttled": 0, "remaining": -1}

    _hb(f"iNat enrich done: taxa={summary['taxa_resolved']} photos={summary['photos_set']} "
        f"no_match={summary['no_match']} throttled={summary['throttled']} "
        f"remaining={summary['remaining']}")
    return summary


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

async def _save_page(bid: uuid.UUID, page_num: int, image_path: str | None,
                     raw_text: str | None, dpi: int | None, confidence: float | None,
                     status: str) -> None:
    """Commit one OCR'd/extracted page immediately, in its own transaction.

    The BookPage row's *existence* (keyed by page_number) is the per-page
    completion marker — no separate ProcessingLog needed. A retry re-derives the
    same page list deterministically from ``split_pdf_smart`` and skips any page
    already present, so OCR is never re-run for a committed page.
    """
    async with async_session() as db:
        db.add(BookPage(
            book_id=bid, page_number=page_num, image_path=image_path,
            raw_text=raw_text, dpi=dpi, ocr_confidence=confidence,
            needs_review=confidence is not None and confidence < 60.0, status=status,
        ))
        await db.commit()


@activity.defn
async def extract_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    attempt = activity.info().attempt
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

    # PDF/OCR path — resumable per page. The page list is deterministic
    # (split_pdf_smart), each page is OCR'd and committed immediately, and a
    # retry skips pages already in the DB. So an interruption (worker restart,
    # cancellation, heartbeat timeout) only re-OCRs the pages not yet committed
    # instead of re-running OCR over the whole book.
    _hb("Downloading PDF from storage")
    pdf_bytes = minio_svc.download_file(file_path)
    _hb("Splitting PDF into pages")
    pages = split_pdf_smart(pdf_bytes)
    n = len(pages)
    _hb(f"Found {n} pages (attempt {attempt})")

    async with async_session() as db:
        if attempt == 1:
            # Fresh run: drop this book's prior pages so everything is re-OCR'd.
            existing = await db.execute(select(BookPage).where(BookPage.book_id == bid))
            for p in existing.scalars().all():
                await db.delete(p)
            await db.commit()
            done_pages: set[int] = set()
        else:
            done_pages = set((await db.execute(
                select(BookPage.page_number).where(BookPage.book_id == bid))).scalars().all())

    if done_pages:
        _hb(f"Resuming extraction: {len(done_pages)}/{n} page(s) already committed")

    for page_data in pages:
        page_num = page_data["page_number"]
        if page_num in done_pages:
            continue
        page_type = page_data["page_type"]
        _hb(f"Page {page_num}/{n} ({page_type})")

        image_path = None
        raw_text = page_data["text"]
        confidence = 100.0 if page_type == "text" else None
        status = "text_extracted" if page_type == "text" else "needs_ocr"

        if page_type == "image" and page_data["image_bytes"]:
            image_path = f"books/{bid}/pages/{page_num:04d}.png"
            minio_svc.upload_file(page_data["image_bytes"], image_path, content_type="image/png")
            text, conf, _ocr_method = await _with_heartbeat(
                ocr_page_with_fallback(page_data["image_bytes"]),
                _hb, f"OCR page {page_num}/{n}",
            )
            raw_text = text
            confidence = conf
            status = "ocr_done"

        await _save_page(bid, page_num, image_path, raw_text,
                         page_data["dpi"], confidence, status)

    # Finalize: rebuild full_text from every committed page, in order. (Pages from
    # both this run and any prior attempt are read straight from the DB.)
    async with async_session() as db:
        page_texts = (await db.execute(
            select(BookPage.raw_text).where(BookPage.book_id == bid)
            .order_by(BookPage.page_number))).scalars().all()
        full_text = "\n\n".join(t for t in page_texts if t)
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = full_text
        book.wizard_step = 3
        book.status = "extracted"
        db.add(ProcessingLog(book_id=bid, step="extract", status="completed",
                             details={"total_pages": n, "text_length": len(full_text)}))
        await db.commit()

    _hb(f"extracted: {n} pages, {len(full_text)} chars")
    return {"total_pages": n, "text_length": len(full_text)}


# ──────────────────────────────────────────────────────────────────────
# Step 2b: Cleanup
# ──────────────────────────────────────────────────────────────────────

# Per-chunk text markers for resumable LLM cleanup/translation. Each marker
# stores one chunk's transformed output keyed by its 1-based index; they're
# bulky (~chunk-sized) but transient — deleted once full_text is persisted.
_CLEANUP_CHUNK_STEP = "cleanup:chunk"
_TRANSLATE_CHUNK_STEP = "translate:chunk"


async def _load_chunk_outputs(bid: uuid.UUID, step: str) -> dict[int, str]:
    """Load per-chunk transformed outputs persisted by an earlier attempt:
    ``{1-based-idx: text}``. Used to resume a chunked transform without re-sending
    completed chunks to the LLM."""
    async with async_session() as db:
        logs = (await db.execute(select(ProcessingLog).where(
            ProcessingLog.book_id == bid, ProcessingLog.step == step))).scalars().all()
    out: dict[int, str] = {}
    for lg in logs:
        if lg.details and lg.details.get("chunk") is not None:
            out[lg.details["chunk"]] = lg.details.get("text", "")
    return out


def _make_chunk_saver(bid: uuid.UUID, step: str):
    """Build an async ``on_chunk_done(idx, text)`` hook that commits one chunk's
    output atomically, so a transform can resume past it after an interruption."""
    async def _save(idx: int, text: str) -> None:
        async with async_session() as db:
            db.add(ProcessingLog(book_id=bid, step=step, status="completed",
                                 details={"chunk": idx, "text": text}))
            await db.commit()
    return _save


async def _clear_chunk_markers(bid: uuid.UUID, step: str) -> None:
    async with async_session() as db:
        await db.execute(delete(ProcessingLog).where(
            ProcessingLog.book_id == bid, ProcessingLog.step == step))
        await db.commit()


@activity.defn
async def cleanup_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    attempt = activity.info().attempt
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
        # Resumable per chunk. full_text is only overwritten at the very end, so
        # `cleaned` (hence the chunk boundaries) is stable across retries.
        if attempt == 1:
            await _clear_chunk_markers(bid, _CLEANUP_CHUNK_STEP)
            done_outputs: dict[int, str] = {}
        else:
            done_outputs = await _load_chunk_outputs(bid, _CLEANUP_CHUNK_STEP)
            if done_outputs:
                _hb(f"Resuming cleanup: {len(done_outputs)} chunk(s) already done")
        _hb(f"Sending to LLM for OCR artifact cleanup (chunked, {len(cleaned)} chars)")
        cleaned = await transform_text_chunked(
            cleaned, system_prompt=CLEANUP_SYSTEM_PROMPT, task="text_cleanup", cb=_hb,
            done_outputs=done_outputs,
            on_chunk_done=_make_chunk_saver(bid, _CLEANUP_CHUNK_STEP),
        )
        _hb("LLM cleanup complete")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = cleaned
        if use_llm:
            # Drop the bulky per-chunk text markers now that full_text holds them.
            await db.execute(delete(ProcessingLog).where(
                ProcessingLog.book_id == bid, ProcessingLog.step == _CLEANUP_CHUNK_STEP))
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
    attempt = activity.info().attempt
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

    # Resumable per chunk. full_text is only overwritten at the very end, so
    # `normalized` (hence the chunk boundaries) is stable across retries.
    if attempt == 1:
        await _clear_chunk_markers(bid, _TRANSLATE_CHUNK_STEP)
        done_outputs: dict[int, str] = {}
    else:
        done_outputs = await _load_chunk_outputs(bid, _TRANSLATE_CHUNK_STEP)
        if done_outputs:
            _hb(f"Resuming translation: {len(done_outputs)} chunk(s) already done")

    _hb(f"Sending to LLM for archaic language translation (chunked, {len(normalized)} chars)")
    translated = await transform_text_chunked(
        normalized, system_prompt=TRANSLATE_SYSTEM_PROMPT, task="translation", cb=_hb,
        done_outputs=done_outputs,
        on_chunk_done=_make_chunk_saver(bid, _TRANSLATE_CHUNK_STEP),
    )
    _hb(f"Translated: {len(translated)} chars")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.full_text = translated
        book.wizard_step = 4
        await db.execute(delete(ProcessingLog).where(
            ProcessingLog.book_id == bid, ProcessingLog.step == _TRANSLATE_CHUNK_STEP))
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

# Per-section completion marker (ProcessingLog step) for resumable recipe
# extraction — the recipe counterpart of _PLANT_CHUNK_STEP.
_RECIPE_SECTION_STEP = "extract_recipes:section"


async def _save_recipe_section(bid: uuid.UUID, section_index: int, extracted: list) -> int:
    """Persist one recipe section's recipes + ingredients + its completion marker,
    atomically. Mirrors :func:`_save_plant_chunk`.

    The marker (ProcessingLog ``extract_recipes:section``) is committed in the SAME
    transaction as the rows, so a section is either fully saved-and-marked or not at
    all. A later retry skips marked sections, so no duplicates are created and no
    already-extracted section is ever re-sent to the LLM.
    """
    recipes_count = 0
    async with async_session() as db:
        for er in extracted:
            recipe = Recipe(
                book_id=bid, name=er.name, category=_clip(er.category, 30),
                original_text=er.original_text, qdrant_collection=QDRANT_COLLECTION,
            )
            db.add(recipe)
            await db.flush()
            for ing in er.ingredients:
                db.add(RecipeIngredient(
                    recipe_id=recipe.id, name=ing.name,
                    original_name=ing.original, amount=ing.amount, unit=_clip(ing.unit, 50),
                ))
            recipes_count += 1
        db.add(ProcessingLog(
            book_id=bid, step=_RECIPE_SECTION_STEP, status="completed",
            details={"section": section_index, "recipes": recipes_count},
        ))
        await db.commit()
    return recipes_count


async def _mark_recipe_section_failed(bid: uuid.UUID, section_index: int, error: str):
    """Record that a section could not be extracted so a retry skips it instead of
    re-sending it to the LLM forever. The section gets another chance on a fresh
    (attempt 1) re-run, which clears these markers."""
    async with async_session() as db:
        db.add(ProcessingLog(
            book_id=bid, step=_RECIPE_SECTION_STEP, status="failed",
            details={"section": section_index, "error": (error or "")[:500]},
        ))
        await db.commit()


@activity.defn
async def extract_recipes_activity(book_id: str) -> dict:
    """Parse recipe sections (BookSection ``recipe_block``) into Recipe +
    RecipeIngredient rows.

    Resumable by design (mirrors :func:`extract_plant_entries_activity`). Each
    section's recipes are committed to the DB *immediately*, together with a
    per-section completion marker, in one transaction. So if the activity is
    interrupted — worker restart, cancellation, heartbeat timeout, or a transient
    LLM error — the retry resumes from the next unprocessed section instead of
    re-extracting the whole book and re-spending tokens on sections already done.

    Fresh vs. resume is keyed on ``activity.info().attempt``:
      * attempt 1  -> fresh run: drop this book's prior recipes + stale section
                      markers, then process every section.
      * attempt >1 -> retry: keep committed sections, skip them, do the rest.
    A single section that fails to parse is logged + skipped (not fatal) so one bad
    section never dooms the whole book.
    """
    bid = uuid.UUID(book_id)
    attempt = activity.info().attempt

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

    n = len(section_data)
    _hb(f"Recipe extraction: {n} section(s) (attempt {attempt})")

    async with async_session() as db:
        if attempt == 1:
            # Fresh run: drop this book's prior recipes AND stale section markers
            # so everything is re-derived cleanly. (Recipe delete cascades to
            # RecipeIngredient.)
            await db.execute(delete(Recipe).where(Recipe.book_id == bid))
            await db.execute(delete(ProcessingLog).where(
                ProcessingLog.book_id == bid, ProcessingLog.step == _RECIPE_SECTION_STEP))
            await db.commit()
            done_sections: set[int] = set()
        else:
            logs = (await db.execute(select(ProcessingLog).where(
                ProcessingLog.book_id == bid,
                ProcessingLog.step == _RECIPE_SECTION_STEP,
            ))).scalars().all()
            # Only treat COMPLETED sections as done. A "failed" marker must NOT
            # count as done — otherwise a section that died on a transient blip is
            # skipped forever on retry and its recipes are lost. (Re-running a
            # failed section is safe: fresh attempt-1 runs wipe prior recipes, and
            # a resumed section had nothing committed to duplicate.)
            done_sections = {
                lg.details["section"] for lg in logs
                if lg.status == "completed" and lg.details
                and lg.details.get("section") is not None
            }

    if done_sections:
        _hb(f"Resuming recipe extraction: {len(done_sections)}/{n} section(s) already committed")

    failed = 0
    for i, sd in enumerate(section_data):
        if i in done_sections:
            continue
        _hb(f"Section {i+1}/{n}: {sd['title']} ({len(sd['text'])} chars)")
        try:
            extracted = await _with_heartbeat(
                extract_recipes_from_section(
                    sd["text"], book_title=book_title,
                    recipe_pattern=sd["recipe_pattern"], progress_callback=_hb,
                ),
                _hb, f"Section {i+1}/{n}",
            )
        except asyncio.CancelledError:
            # Temporal is tearing the activity down (timeout / worker shutdown).
            # Committed sections are safe; let it propagate so the retry resumes.
            raise
        except Exception as e:
            failed += 1
            logger.exception(f"Section {i+1} extraction failed")
            _hb(f"Section {i+1}: ERROR - {e} (skipped)")
            await _mark_recipe_section_failed(bid, i, str(e))
            continue
        try:
            rc = await _save_recipe_section(bid, i, extracted)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A save failure (e.g. an over-long value or constraint violation) must
            # not doom the whole book: skip this section, mark it failed, keep going.
            failed += 1
            logger.exception(f"Section {i+1} save failed")
            _hb(f"Section {i+1}: SAVE ERROR - {e} (skipped)")
            await _mark_recipe_section_failed(bid, i, str(e))
            continue
        _hb(f"Section {i+1}/{n}: saved {rc} recipes")

    # Finalize: count this book's committed recipes across fresh + resumed sections.
    async with async_session() as db:
        recipes_count = (await db.execute(
            select(func.count()).select_from(Recipe).where(Recipe.book_id == bid))).scalar() or 0

    # Zero recipes is LEGITIMATE for a pure-monograph herbalism book — but zero
    # recipes WHILE sections failed means a transient problem (e.g. a network/LLM
    # blip) wiped every section. Raise a RETRYABLE error (RuntimeError, not the
    # non-retryable ValueError) so Temporal retries; the resume logic skips the
    # sections that did complete and re-runs only the failed ones.
    if recipes_count == 0 and failed > 0:
        raise RuntimeError(
            f"All {failed} recipe section(s) failed and no recipes were extracted; "
            "retrying so a transient failure does not finalize the book empty"
        )

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 6
        book.status = "recipes_extracted"
        db.add(ProcessingLog(book_id=bid, step="extract_recipes", status="completed",
                             details={"recipes_count": recipes_count,
                                      "sections_processed": n,
                                      "sections_failed": failed}))
        await db.commit()

    return {"recipes_count": recipes_count, "sections_processed": n,
            "sections_failed": failed}


# ──────────────────────────────────────────────────────────────────────
# Herbalism branch: Extract Plant Monographs
# ──────────────────────────────────────────────────────────────────────

def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _clip(value: str | None, maxlen: int) -> str | None:
    """Trim an LLM free-text value so it fits a narrow ``VARCHAR(n)`` column.

    The short plant-fact columns (``part``, ``preparation``, ``compound_group``,
    ``status``, ``severity`` …) are meant to hold a controlled-vocabulary term,
    but the model sometimes returns a whole phrase — which overflows the column
    and aborts the whole chunk's INSERT (asyncpg ``StringDataRightTruncationError``).
    Clipping is lossless in practice: the full, verbatim wording is always also
    stored in a companion ``Text`` column (``original_text`` / ``indications`` /
    ``dosage`` …), so nothing from the source is dropped.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:maxlen]


async def _resolve_plant(db, ep, kingdom: str = "растение") -> Plant:
    """Find an existing plant (by Latin name, else Russian name) or create one.

    Plants are shared across source books — a herbal enriches an existing plant
    rather than duplicating it. Identity fields are filled in only when missing
    so the first/most-complete source wins; ``parts_used`` is merged.

    ``kingdom`` (растение | гриб) tags a NEWLY created row — set when a fungi
    guide is being ingested. An already-existing row keeps its kingdom (the first
    source that created it wins, like the other identity fields).
    """
    plant = None
    if ep.name_latin:
        plant = (await db.execute(
            select(Plant).where(func.lower(Plant.name_latin) == _norm(ep.name_latin))
        )).scalars().first()

    # Latin binomial dedup (exact-string lookup missed): the scientific name is
    # the authoritative identity, so a row whose name_latin agrees on genus +
    # species epithet — ignoring author citation ("L.") and case ("gratiola
    # officinalis" vs "Gratiola officinalis L.") — is the SAME species. This is
    # the forward-fix that stops the herbarium re-accreting latin duplicates.
    if plant is None and ep.name_latin:
        key = _latin_key(ep.name_latin)
        if key:
            candidates = [
                p for p in (await db.execute(select(Plant))).scalars().all()
                if _latin_key(p.name_latin) == key
            ]
            if candidates:
                # Prefer the candidate with the most complete latin name (author
                # citation present) so enrichment lands on the canonical row.
                plant = max(candidates, key=lambda p: len(p.name_latin or ""))

    if plant is None and ep.name:
        plant = (await db.execute(
            select(Plant).where(func.lower(Plant.name) == _norm(ep.name))
        )).scalars().first()

    # Fuzzy identity dedup (exact lookups missed): a bare-genus stub, an inflected
    # epithet, or a genus+epithet form of an existing plant are the same plant.
    # Only merge when UNambiguous — if several existing plants share the genus
    # (e.g. two Шалфей species), a wrong auto-merge is worse than a visible stub,
    # so we fall through and create a new row instead of guessing.
    if plant is None and ep.name:
        candidates = [
            p for p in (await db.execute(select(Plant))).scalars().all()
            if same_plant_identity(ep.name, p.name)
        ]
        if len(candidates) == 1:
            plant = candidates[0]
            # Promote a barer stub name to the fuller incoming one, keeping the
            # old spelling as a historical name (mirrors a manual merge).
            if is_more_specific(ep.name, plant.name):
                hist = list(plant.names_historical or [])
                if plant.name and plant.name not in hist:
                    hist.append(plant.name)
                plant.names_historical = hist
                plant.name = ep.name

    if plant is None:
        plant = Plant(name=ep.name, name_latin=ep.name_latin or None,
                      kingdom=kingdom,
                      qdrant_collection=QDRANT_PLANTS_COLLECTION)
        db.add(plant)
        await db.flush()

    # Fill identity fields only if currently empty (don't clobber a richer source).
    if not plant.name_latin and ep.name_latin:
        plant.name_latin = ep.name_latin
    if not plant.family and ep.family:
        plant.family = ep.family
    if not plant.family_latin and ep.family_latin:
        plant.family_latin = ep.family_latin
    if not plant.description and ep.description:
        plant.description = ep.description
    if ep.is_toxic:
        plant.is_toxic = True
    # Merge parts_used and historical names (union, order-preserving).
    if ep.parts_used:
        merged = list(plant.parts_used or [])
        for p in ep.parts_used:
            if p and p not in merged:
                merged.append(p)
        plant.parts_used = merged
    if ep.names_historical:
        merged = list(plant.names_historical or [])
        for n in ep.names_historical:
            if n and n not in merged:
                merged.append(n)
        plant.names_historical = merged
    return plant


# Per-chunk progress marker. Written atomically with each chunk's plant rows so
# a retry can tell exactly which chunks are already committed and skip them.
_PLANT_CHUNK_STEP = "extract_plant_entries:chunk"


async def _save_plant_chunk(bid: uuid.UUID, chunk_index: int, plants: list, action_map: dict, kingdom: str = "растение") -> tuple[int, int]:
    """Persist one chunk's extracted plants + its completion marker, atomically.

    The marker (ProcessingLog ``extract_plant_entries:chunk``) is committed in the
    SAME transaction as the rows, so a chunk is either fully saved-and-marked or
    not at all. A later retry skips marked chunks, so no duplicates are created
    and no already-extracted chunk is ever re-sent to the LLM.
    """
    plants_count = 0
    uses_count = 0
    async with async_session() as db:
        for ep in plants:
            plant = await _resolve_plant(db, ep, kingdom)
            for u in ep.medicinal_uses:
                db.add(PlantMedicinalUse(
                    plant_id=plant.id, part=_clip(u.part, 50),
                    action_id=action_map.get(_norm(u.action)),
                    action_raw=u.action or None,
                    indications=u.indications or None,
                    preparation=_clip(u.preparation, 50),
                    dosage=u.dosage or None,
                    contraindications=u.contraindications or None,
                    original_text=u.original_text or None,
                    source_book_id=bid,
                ))
                uses_count += 1
            for c in ep.compounds:
                db.add(PlantCompound(
                    plant_id=plant.id, compound=c.compound,
                    compound_group=_clip(c.compound_group, 60),
                    part=_clip(c.part, 50), notes=c.notes or None, source_book_id=bid,
                ))
            for h in ep.harvests:
                db.add(PlantHarvest(
                    plant_id=plant.id, part=_clip(h.part, 50), season=h.season or None,
                    method=h.method or None, original_text=h.original_text or None,
                    source_book_id=bid,
                ))
            for hb in ep.habitats:
                db.add(PlantHabitat(
                    plant_id=plant.id, region=hb.region or None, biotope=hb.biotope or None,
                    status=_clip(hb.status, 60), original_text=hb.original_text or None,
                    source_book_id=bid,
                ))
            for t in ep.toxicities:
                db.add(PlantToxicity(
                    plant_id=plant.id, toxic_parts=t.toxic_parts or None,
                    symptoms=t.symptoms or None, antidote=t.antidote or None,
                    severity=_clip(t.severity, 30), original_text=t.original_text or None,
                    source_book_id=bid,
                ))
            for cu in ep.culinary_uses:
                db.add(PlantCulinaryUse(
                    plant_id=plant.id, part=_clip(cu.part, 50),
                    edibility=_clip(cu.edibility, 20),
                    preparation=_clip(cu.preparation, 60),
                    use=cu.use or None, season=cu.season or None,
                    caution=cu.caution or None, original_text=cu.original_text or None,
                    source_book_id=bid,
                ))
            db.add(PlantBookMention(
                plant_id=plant.id, book_id=bid, original_name=ep.name,
                original_text=ep.original_text or None,
            ))
            plants_count += 1
        db.add(ProcessingLog(
            book_id=bid, step=_PLANT_CHUNK_STEP, status="completed",
            details={"chunk": chunk_index, "plants": plants_count, "uses": uses_count},
        ))
        await db.commit()
    return plants_count, uses_count


async def _mark_plant_chunk_failed(bid: uuid.UUID, chunk_index: int, error: str):
    """Record that a chunk could not be extracted so a retry skips it instead of
    re-sending it to the LLM forever. The chunk gets another chance on a fresh
    (attempt 1) re-run, which clears these markers."""
    async with async_session() as db:
        db.add(ProcessingLog(
            book_id=bid, step=_PLANT_CHUNK_STEP, status="failed",
            details={"chunk": chunk_index, "error": (error or "")[:500]},
        ))
        await db.commit()


@activity.defn
async def extract_plant_entries_activity(book_id: str) -> dict:
    """Herbalism counterpart of extract_recipes: parse plant monographs into
    Plant + medicinal-use/compound/harvest/habitat/toxicity rows.

    Resumable by design. The book is split into LLM-sized chunks and each chunk's
    plants are committed to the DB *immediately*, together with a per-chunk
    completion marker, in one transaction. So if the activity is interrupted —
    worker restart, cancellation, heartbeat timeout, or a transient LLM error —
    the retry resumes from the next unprocessed chunk instead of re-reading the
    whole book and re-spending tokens on chunks already done.

    Fresh vs. resume is keyed on whether ANY chunk is already committed:
      * no completed-chunk markers -> fresh run: drop this book's prior facts +
                      stale chunk markers, then process every chunk.
      * some completed-chunk markers -> resume: keep committed chunks, skip them,
                      do the rest. This holds even across a brand-new workflow run
                      started at this step (e.g. re-running a big book that ran out
                      of retries one chunk short) — partial progress is never
                      thrown away. To force a clean re-extract, clear this book's
                      plant-chunk ProcessingLog markers first.
    A single chunk that fails to parse is logged + skipped (not fatal) so one bad
    chunk never dooms the whole book.
    """
    bid = uuid.UUID(book_id)
    attempt = activity.info().attempt

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text available")
        book_title = book.title
        full_text = book.full_text
        # A fungi guide (domain=fungi) tags its rows as грибы so they don't
        # pollute the "plants" namespace; everything else is растение.
        kingdom = "гриб" if (book.domain or "").lower() == "fungi" else "растение"

    chunks = _split_into_chunks(full_text)
    n = len(chunks)
    _hb(f"Plant extraction: {n} chunk(s) from {len(full_text)} chars (attempt {attempt})")

    async with async_session() as db:
        # Completed-chunk markers from ANY prior run (this attempt, an earlier
        # retry, or a previous — possibly failed — workflow run). Only COMPLETED
        # chunks count: a "failed" marker must NOT, or a chunk that died on a
        # transient blip would be skipped forever and its plants lost.
        logs = (await db.execute(select(ProcessingLog).where(
            ProcessingLog.book_id == bid,
            ProcessingLog.step == _PLANT_CHUNK_STEP,
        ))).scalars().all()
        done_chunks: set[int] = {
            lg.details["chunk"] for lg in logs
            if lg.status == "completed" and lg.details
            and lg.details.get("chunk") is not None
        }

        # Fresh slate ONLY when nothing is committed yet (attempt 1 of a book with
        # no completed chunks). Once any chunk is committed we resume instead — even
        # across a brand-new workflow run started at this step — so re-running a big
        # book that exhausted its retries finishes the REMAINING chunks rather than
        # nuking thousands of committed rows and re-spending tokens on done chunks.
        if attempt == 1 and not done_chunks:
            for tbl in (PlantMedicinalUse, PlantCompound, PlantHarvest, PlantHabitat,
                        PlantToxicity, PlantCulinaryUse):
                await db.execute(delete(tbl).where(tbl.source_book_id == bid))
            await db.execute(delete(PlantBookMention).where(PlantBookMention.book_id == bid))
            await db.execute(delete(ProcessingLog).where(
                ProcessingLog.book_id == bid, ProcessingLog.step == _PLANT_CHUNK_STEP))
            await db.commit()

        # Normalization map: action term / modern synonym -> MedicinalAction.id
        actions = (await db.execute(select(MedicinalAction))).scalars().all()
        action_map: dict[str, uuid.UUID] = {}
        for a in actions:
            action_map[_norm(a.name)] = a.id
            if a.name_modern:
                action_map[_norm(a.name_modern)] = a.id

    if done_chunks:
        _hb(f"Resuming plant extraction: {len(done_chunks)}/{n} chunk(s) already committed")

    failed = 0
    for i, chunk in enumerate(chunks):
        if i in done_chunks:
            continue
        _hb(f"Plant chunk {i+1}/{n}: {len(chunk)} chars")
        try:
            plants = await _with_heartbeat(
                _extract_single(chunk, book_title), _hb, f"Plant chunk {i+1}/{n}"
            )
        except asyncio.CancelledError:
            # Temporal is tearing the activity down (timeout / worker shutdown).
            # Committed chunks are safe; let it propagate so the retry resumes.
            raise
        except Exception as e:
            activity.logger.warning(f"Plant chunk {i+1}/{n} extraction failed, skipping: {e}")
            await _mark_plant_chunk_failed(bid, i, str(e))
            failed += 1
            continue
        try:
            pc, uc = await _save_plant_chunk(bid, i, plants, action_map, kingdom)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A save failure (e.g. a stray over-long value slipping past _clip, or a
            # constraint violation) must not doom the whole book: roll past this
            # chunk, mark it failed, and keep going so the rest still commits.
            activity.logger.exception(f"Plant chunk {i+1}/{n} save failed, skipping")
            await _mark_plant_chunk_failed(bid, i, str(e))
            failed += 1
            continue
        _hb(f"Plant chunk {i+1}/{n}: saved {pc} plants ({uc} uses)")

    # Finalize: count this book's committed facts for an accurate total across
    # both freshly-processed and resumed chunks.
    async with async_session() as db:
        plants_count = (await db.execute(
            select(func.count()).select_from(PlantBookMention)
            .where(PlantBookMention.book_id == bid))).scalar() or 0
        uses_count = (await db.execute(
            select(func.count()).select_from(PlantMedicinalUse)
            .where(PlantMedicinalUse.source_book_id == bid))).scalar() or 0

    # Zero plants WHILE chunks failed means a transient problem (e.g. a network/LLM
    # blip) wiped every chunk. Raise a RETRYABLE error (RuntimeError, not the
    # non-retryable ValueError) so Temporal retries; resume skips the chunks that
    # did complete and re-runs only the failed ones. Don't write the "completed"
    # marker yet — let the retry finalize once chunks actually succeed.
    if plants_count == 0 and failed > 0:
        raise RuntimeError(
            f"All {failed} plant chunk(s) failed and no plants were extracted; "
            "retrying so a transient failure does not finalize the book empty"
        )

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 6
        book.status = "plants_extracted"
        db.add(ProcessingLog(book_id=bid, step="extract_plant_entries", status="completed",
                             details={"plants_count": plants_count, "uses_count": uses_count,
                                      "failed_chunks": failed}))
        await db.commit()

    # Genuine zero (no failures) — the book has no plant monographs at all. That's
    # a real input problem worth surfacing as non-retryable.
    if plants_count == 0:
        raise ValueError("No plants extracted")
    return {"plants_count": plants_count, "uses_count": uses_count, "failed_chunks": failed}


# ──────────────────────────────────────────────────────────────────────
# Reference branch: Build compound vocabulary + normalize the corpus
# ──────────────────────────────────────────────────────────────────────

_COMPOUND_CHUNK_STEP = "extract_vocabulary:chunk"


async def _upsert_compound(db, *, name, name_latin="", compound_class="", definition="",
                           synonyms=None, original_text="", source_book_id=None, parent_id=None):
    """Insert a vocabulary term or enrich an existing one (dedup by lower(name)).

    The vocabulary is cumulative across books — a term is never duplicated and
    existing values are never clobbered; only empty fields are filled and
    synonyms are unioned. Returns the (new or existing) ``Compound``.
    """
    name = (name or "").strip()
    if not name:
        return None
    existing = (await db.execute(
        select(Compound).where(func.lower(Compound.name) == name.lower())
    )).scalars().first()
    if existing is None:
        c = Compound(
            name=name, name_latin=(name_latin or None),
            compound_class=_clip(compound_class, 60), definition=(definition or None),
            synonyms=(synonyms or None), original_text=(original_text or None),
            source_book_id=source_book_id, parent_id=parent_id,
        )
        db.add(c)
        await db.flush()
        return c
    if not existing.name_latin and name_latin:
        existing.name_latin = name_latin
    if not existing.compound_class and compound_class:
        existing.compound_class = _clip(compound_class, 60)
    if not existing.definition and definition:
        existing.definition = definition
    if not existing.original_text and original_text:
        existing.original_text = original_text
    if existing.parent_id is None and parent_id is not None and parent_id != existing.id:
        existing.parent_id = parent_id
    if synonyms:
        merged = list(existing.synonyms or [])
        for s in synonyms:
            if s and s not in merged:
                merged.append(s)
        existing.synonyms = merged
    return existing


async def _save_compound_chunk(bid: uuid.UUID, chunk_index: int, compounds: list) -> tuple[int, int]:
    """Persist one chunk's compounds + occurrences + completion marker, atomically.

    Vocabulary terms are upserted (global, deduped). Each stated plant occurrence
    becomes a normalized ``PlantCompound`` row (raw ``compound`` = the term, plus
    ``compound_id`` already set), with the plant resolved/created via the shared
    ``_resolve_plant`` so a chemistry book cross-links to existing plants.
    """
    vocab_count = 0
    occ_count = 0
    async with async_session() as db:
        for c in compounds:
            parent_id = None
            if c.parent and c.parent.strip().lower() != (c.name or "").strip().lower():
                parent = await _upsert_compound(
                    db, name=c.parent, compound_class=c.compound_class or c.parent,
                    source_book_id=bid)
                parent_id = parent.id if parent else None
            vocab = await _upsert_compound(
                db, name=c.name, name_latin=c.name_latin, compound_class=c.compound_class,
                definition=c.definition, synonyms=c.synonyms, original_text=c.original_text,
                source_book_id=bid, parent_id=parent_id)
            if vocab is None:
                continue
            vocab_count += 1
            for occ in c.occurrences:
                shim = SimpleNamespace(
                    name=occ.plant, name_latin=occ.plant_latin or "", family="",
                    family_latin="", description="", is_toxic=False,
                    parts_used=[], names_historical=[])
                plant = await _resolve_plant(db, shim)
                db.add(PlantCompound(
                    plant_id=plant.id, compound=c.name,
                    compound_group=_clip(c.compound_class, 60),
                    compound_id=vocab.id, part=_clip(occ.part, 50), source_book_id=bid))
                occ_count += 1
        db.add(ProcessingLog(
            book_id=bid, step=_COMPOUND_CHUNK_STEP, status="completed",
            details={"chunk": chunk_index, "compounds": vocab_count, "occurrences": occ_count}))
        await db.commit()
    return vocab_count, occ_count


async def _mark_compound_chunk_failed(bid: uuid.UUID, chunk_index: int, error: str):
    async with async_session() as db:
        db.add(ProcessingLog(
            book_id=bid, step=_COMPOUND_CHUNK_STEP, status="failed",
            details={"chunk": chunk_index, "error": (error or "")[:500]}))
        await db.commit()


@activity.defn
async def extract_vocabulary_activity(book_id: str) -> dict:
    """Reference-domain counterpart of extract_plant_entries: build the compound
    vocabulary from a phytochemistry reference and create normalized
    ``PlantCompound`` occurrences. Resumable per-chunk, same as plant extraction.
    """
    bid = uuid.UUID(book_id)
    attempt = activity.info().attempt

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        if not book.full_text:
            raise ValueError("No text available")
        book_title = book.title
        full_text = book.full_text

    chunks = _split_compound_chunks(full_text)
    n = len(chunks)
    _hb(f"Compound extraction: {n} chunk(s) from {len(full_text)} chars (attempt {attempt})")

    async with async_session() as db:
        if attempt == 1:
            # Fresh run: drop only THIS book's occurrence rows + stale markers. The
            # vocabulary is cumulative/global and is re-upserted (enriched), never
            # deleted, so other books' compound links survive.
            await db.execute(delete(PlantCompound).where(PlantCompound.source_book_id == bid))
            await db.execute(delete(ProcessingLog).where(
                ProcessingLog.book_id == bid, ProcessingLog.step == _COMPOUND_CHUNK_STEP))
            await db.commit()
            done_chunks: set[int] = set()
        else:
            logs = (await db.execute(select(ProcessingLog).where(
                ProcessingLog.book_id == bid,
                ProcessingLog.step == _COMPOUND_CHUNK_STEP,
            ))).scalars().all()
            done_chunks = {
                lg.details["chunk"] for lg in logs
                if lg.status == "completed" and lg.details
                and lg.details.get("chunk") is not None
            }

    if done_chunks:
        _hb(f"Resuming compound extraction: {len(done_chunks)}/{n} chunk(s) already committed")

    failed = 0
    for i, chunk in enumerate(chunks):
        if i in done_chunks:
            continue
        _hb(f"Compound chunk {i+1}/{n}: {len(chunk)} chars")
        try:
            comps = await _compound_with_heartbeat(
                _extract_compound_single(chunk, book_title), _hb, f"Compound chunk {i+1}/{n}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            activity.logger.warning(f"Compound chunk {i+1}/{n} extraction failed, skipping: {e}")
            await _mark_compound_chunk_failed(bid, i, str(e))
            failed += 1
            continue
        try:
            cc, oc = await _save_compound_chunk(bid, i, comps)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            activity.logger.exception(f"Compound chunk {i+1}/{n} save failed, skipping")
            await _mark_compound_chunk_failed(bid, i, str(e))
            failed += 1
            continue
        _hb(f"Compound chunk {i+1}/{n}: saved {cc} compounds ({oc} occurrences)")

    async with async_session() as db:
        vocab_count = (await db.execute(
            select(func.count()).select_from(Compound)
            .where(Compound.source_book_id == bid))).scalar() or 0
        occ_count = (await db.execute(
            select(func.count()).select_from(PlantCompound)
            .where(PlantCompound.source_book_id == bid))).scalar() or 0

    if vocab_count == 0 and failed > 0:
        raise RuntimeError(
            f"All {failed} compound chunk(s) failed and no compounds were extracted; "
            "retrying so a transient failure does not finalize the book empty")

    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.status = "compounds_extracted"
        db.add(ProcessingLog(book_id=bid, step="extract_vocabulary", status="completed",
                             details={"compounds_count": vocab_count,
                                      "occurrences_count": occ_count, "failed_chunks": failed}))
        await db.commit()

    if vocab_count == 0:
        raise ValueError("No compounds extracted")
    return {"compounds_count": vocab_count, "occurrences_count": occ_count, "failed_chunks": failed}


@activity.defn
async def normalize_corpus_activity(book_id: str) -> dict:
    """Corpus-wide, idempotent normalize of every ``PlantCompound.compound`` to a
    ``compound_id`` against the current vocabulary (compound analog of relink)."""
    bid = uuid.UUID(book_id)
    _hb("Normalizing plant compounds against the compound vocabulary")
    async with async_session() as db:
        result = await normalize_plant_compounds(db, commit=False)
        db.add(ProcessingLog(book_id=bid, step="normalize_corpus", status="completed", details=result))
        await db.commit()
    _hb(f"Normalized compounds: {result}")
    return result


# ──────────────────────────────────────────────────────────────────────
# Step 6: Match Ingredients
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def match_ingredients_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        recipes = (await db.execute(select(Recipe).where(Recipe.book_id == bid))).scalars().all()
        if not recipes:
            # No recipes is LEGITIMATE: a herbalism book may be pure plant
            # monographs with no medicinal recipes. Nothing to match — no-op
            # instead of failing the whole pipeline (a ValueError here is
            # non-retryable and would permanently kill an otherwise-good book).
            _hb("No recipes to match ingredients for; skipping")
            return {"matched": 0, "new_created": 0, "linked_to_plant": 0, "total": 0}

        _hb(f"Matching ingredients for {len(recipes)} recipes")

        all_ingredients = (await db.execute(select(Ingredient))).scalars().all()
        all_synonyms = (await db.execute(select(IngredientSynonym))).scalars().all()
        name_to_ingredient = {i.canonical_name.lower(): i for i in all_ingredients}
        synonym_to_ingredient = {s.synonym.lower(): s.ingredient_id for s in all_synonyms}

        # Bridge to the herbalism domain: resolve ingredients to known plants so a
        # recipe ingredient can surface that plant's medicinal action. Normalized,
        # alt-name-aware matching across plant name/latin/historical names and the
        # ingredient's recipe name/original_name/canonical name/synonyms.
        plants = (await db.execute(select(Plant))).scalars().all()
        matcher = PlantMatcher(plants)
        ingredient_by_id = {i.id: i for i in all_ingredients}
        syn_by_ing: dict[uuid.UUID, list[str]] = {}
        for s in all_synonyms:
            syn_by_ing.setdefault(s.ingredient_id, []).append(s.synonym)

        def _resolve_plant_id(ri, ingredient_id):
            # Exclude ri.original_name — it is the verbatim recipe fragment with
            # amounts/units and injects noise tokens. Match clean names only.
            ing = ingredient_by_id.get(ingredient_id)
            names = [ri.name]
            if ing is not None:
                names.append(ing.canonical_name)
                names.extend(syn_by_ing.get(ing.id, []))
            return matcher.match(names)

        matched = 0
        new_created = 0
        linked_to_plant = 0
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
                    ingredient_by_id[new_ing.id] = new_ing
                    new_created += 1

                plant_id = _resolve_plant_id(ri, ri.ingredient_id)
                if plant_id:
                    ri.plant_id = plant_id
                    linked_to_plant += 1
                    # Persist the link on the shared Ingredient row too, when empty.
                    ing = ingredient_by_id.get(ri.ingredient_id)
                    if ing is not None and ing.plant_id is None:
                        ing.plant_id = plant_id

            if (ri_idx + 1) % 5 == 0 or ri_idx == len(recipes) - 1:
                _hb(f"Recipe {ri_idx+1}/{len(recipes)}: matched={matched}, new={new_created}, plants={linked_to_plant}")

        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 7
        db.add(ProcessingLog(book_id=bid, step="match_ingredients", status="completed",
                             details={"matched": matched, "new_created": new_created,
                                      "linked_to_plant": linked_to_plant}))
        await db.commit()

    return {"matched": matched, "new_created": new_created,
            "linked_to_plant": linked_to_plant, "total": matched + new_created}


# ──────────────────────────────────────────────────────────────────────
# Step 7: Index to Qdrant
# ──────────────────────────────────────────────────────────────────────

async def _index_sections(db, book) -> int:
    """Embed the prose text of meaningful book sections into the sections
    collection, so downstream agents can retrieve passages that are NOT clean
    recipes/plants (introductions, historical notes, general descriptions, and
    the connective prose inside recipe blocks).

    Runs for BOTH domains. ``BookSection`` stores only line ranges, so the text
    is reconstructed from ``book.full_text`` (same as analyze/extract). Long
    sections are split with the shared chunker (each chunk ≤ MAX_CHARS_PER_CALL,
    comfortably under the embedder's 16K-char truncation, so no content is lost).

    Idempotent: point IDs are a deterministic uuid5 of (section_id, chunk_index),
    and this book's points are deleted first — so a re-index replaces cleanly.
    Returns the number of points upserted; 0 (no-op) if the book has no indexable
    sections, which is not an error.
    """
    bid = book.id
    if not book.full_text:
        return 0

    sections = (await db.execute(
        select(BookSection).where(BookSection.book_id == bid).order_by(BookSection.start_line)
    )).scalars().all()
    sections = [s for s in sections if s.section_type in _INDEXABLE_SECTION_TYPES]
    if not sections:
        return 0

    _hb("Ensuring sections Qdrant collection exists")
    await qdrant_svc.ensure_collection(QDRANT_SECTIONS_COLLECTION)
    await qdrant_svc.delete_by_filter(QDRANT_SECTIONS_COLLECTION, "book_id", str(bid))

    lines = book.full_text.split("\n")
    domain = book.domain or "recipes"
    points = []
    for s in sections:
        section_text = "\n".join(
            lines[max(0, (s.start_line or 1) - 1):min(s.end_line or 0, len(lines))]
        ).strip()
        if not section_text:
            continue
        chunks = _split_into_chunks(section_text)
        for ci, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            embed_text = f"{s.title}\n\n{chunk}" if s.title else chunk
            _hb(f"Embedding section '{s.section_type}' chunk {ci+1}/{len(chunks)}")
            embedding = await create_embedding(embed_text)
            point = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.id}:{ci}")),
                "dense": embedding["dense"],
                "payload": {
                    "book_id": str(bid),
                    "book_title": book.title,
                    "author": book.author or "",
                    "year": book.year,
                    "section_type": s.section_type,
                    "title": s.title or "",
                    "content": chunk,
                    "chunk_index": ci,
                    "domain": domain,
                    "language": book.language or "modern_ru",
                },
            }
            if "sparse" in embedding and embedding["sparse"]:
                point["sparse"] = embedding["sparse"]
            points.append(point)

    if not points:
        return 0

    _hb(f"Upserting {len(points)} section points to Qdrant")
    for i in range(0, len(points), 50):
        await qdrant_svc.upsert_points(QDRANT_SECTIONS_COLLECTION, points[i:i + 50])
        _hb(f"Upserted section batch {i//50 + 1}")
    return len(points)


async def _index_recipes(db, book) -> int:
    """Index this book's recipes into the recipes Qdrant collection.

    Shared by both domains: the recipes pipeline indexes culinary/alcohol recipes,
    and the herbalism pipeline calls this too so the medicinal recipes (decoctions,
    teas, herbal collections) extracted from herbalism books are searchable in the
    same recipes collection — distinguishable downstream by their book's domain.

    Returns the number of points upserted. Does NOT commit book status or write a
    ProcessingLog — the caller owns finalization. Zero recipes is a no-op (returns
    0) rather than an error, so a herbalism book with only plant monographs is fine.
    """
    bid = book.id
    _hb("Ensuring recipes Qdrant collection exists")
    await qdrant_svc.ensure_collection(QDRANT_COLLECTION)
    await qdrant_svc.delete_by_filter(QDRANT_COLLECTION, "book_id", str(bid))

    recipes = (await db.execute(select(Recipe).where(Recipe.book_id == bid))).scalars().all()
    if not recipes:
        return 0

    _hb(f"Indexing {len(recipes)} recipes")
    points = []
    for i, recipe in enumerate(recipes):
        ingredients = [ri.name for ri in (await db.execute(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
        )).scalars().all()]

        embed_text = f"Рецепт: {recipe.name}\n\nСодержание: {recipe.original_text or ''}"
        _hb(f"Embedding recipe {i+1}/{len(recipes)}: {recipe.name}")
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

    _hb("Upserting recipe points to Qdrant")
    for i in range(0, len(points), 50):
        await qdrant_svc.upsert_points(QDRANT_COLLECTION, points[i:i + 50])
        _hb(f"Upserted recipe batch {i//50 + 1}")

    return len(points)


async def _index_plants(db, book) -> dict:
    """Index this book's plants into the herbalism Qdrant collection.

    Embeds a compact monograph summary (name + medicinal actions + indications)
    so the herbalism search can retrieve plants by symptom/action.
    """
    bid = book.id
    _hb("Ensuring plants Qdrant collection exists")
    await qdrant_svc.ensure_collection(QDRANT_PLANTS_COLLECTION)
    await qdrant_svc.delete_by_filter(QDRANT_PLANTS_COLLECTION, "book_id", str(bid))

    plant_ids = [m.plant_id for m in (await db.execute(
        select(PlantBookMention).where(PlantBookMention.book_id == bid)
    )).scalars().all()]
    plant_ids = list(dict.fromkeys(plant_ids))  # dedupe, keep order
    if not plant_ids:
        raise ValueError("No plants to index")

    _hb(f"Indexing {len(plant_ids)} plants")
    points = []
    for i, pid in enumerate(plant_ids):
        plant = (await db.execute(select(Plant).where(Plant.id == pid))).scalar_one()
        uses = (await db.execute(
            select(PlantMedicinalUse).where(PlantMedicinalUse.plant_id == pid)
        )).scalars().all()
        actions = sorted({u.action_raw for u in uses if u.action_raw})
        indications = sorted({u.indications for u in uses if u.indications})

        culinary = (await db.execute(
            select(PlantCulinaryUse).where(PlantCulinaryUse.plant_id == pid)
        )).scalars().all()
        edibility = sorted({c.edibility for c in culinary if c.edibility})
        edible_parts = sorted({c.part for c in culinary if c.part})
        food_uses = sorted({c.use for c in culinary if c.use})
        # One compact culinary line for the embedding so foraging questions
        # ("что съедобно", "что едят сырым") retrieve the plant.
        culinary_line = ""
        if culinary:
            bits = []
            if edibility:
                bits.append(", ".join(edibility))
            if edible_parts:
                bits.append("части: " + ", ".join(edible_parts))
            if food_uses:
                bits.append("в пищу: " + "; ".join(food_uses))
            culinary_line = "\nСъедобность: " + ". ".join(bits)

        kind_label = "Гриб" if (plant.kingdom or "").lower() == "гриб" else "Растение"
        embed_text = (
            f"{kind_label}: {plant.name}"
            + (f" ({plant.name_latin})" if plant.name_latin else "")
            + (f"\nДействие: {', '.join(actions)}" if actions else "")
            + (f"\nПрименяется при: {'; '.join(indications)}" if indications else "")
            + culinary_line
            + (f"\nОписание: {plant.description}" if plant.description else "")
        )
        _hb(f"Embedding {i+1}/{len(plant_ids)}: {plant.name}")
        embedding = await create_embedding(embed_text)
        point = {
            "id": str(plant.id),
            "dense": embedding["dense"],
            "payload": {
                "name": plant.name,
                "name_latin": plant.name_latin or "",
                "family": plant.family or "",
                "actions": actions,
                "indications": indications,
                "parts_used": plant.parts_used or [],
                "is_toxic": plant.is_toxic,
                "kingdom": plant.kingdom or "растение",
                "edibility": edibility,
                "edible_parts": edible_parts,
                "source_book": book.title,
                "book_id": str(bid),
            },
        }
        if "sparse" in embedding and embedding["sparse"]:
            point["sparse"] = embedding["sparse"]
        points.append(point)

        plant.qdrant_point_id = str(plant.id)
        plant.qdrant_collection = QDRANT_PLANTS_COLLECTION

    _hb("Upserting points to Qdrant")
    for i in range(0, len(points), 50):
        await qdrant_svc.upsert_points(QDRANT_PLANTS_COLLECTION, points[i:i + 50])
        _hb(f"Upserted batch {i//50 + 1}")

    # Herbalism books also yield medicinal recipes (decoctions/teas/collections).
    # Index them into the recipes collection so they're searchable alongside
    # culinary/alcohol recipes. No-op (0) if this book has only plant monographs.
    recipe_points = await _index_recipes(db, book)
    if recipe_points:
        _hb(f"Indexed {recipe_points} medicinal recipe(s) from this herbalism book")

    # Also embed the book's free-text sections (intro/appendix/other/recipe-block
    # prose) so agents can retrieve passages that aren't structured plants/recipes.
    section_points = await _index_sections(db, book)
    if section_points:
        _hb(f"Indexed {section_points} section passage(s)")

    book.wizard_step = 8
    book.status = "indexed"
    db.add(ProcessingLog(book_id=bid, step="index", status="completed",
                         details={"points_indexed": len(points),
                                  "collection": QDRANT_PLANTS_COLLECTION,
                                  "recipe_points": recipe_points,
                                  "section_points": section_points}))
    await db.commit()

    # This book just added/enriched plants — relink existing recipe ingredients
    # so the recipe↔herbarium cross-links pick up the new species. Cheap,
    # in-memory name matching; safe to run on the full corpus each time.
    _hb("Relinking recipe ingredients to plants")
    relink = await relink_recipe_ingredients(db)
    _hb(f"Relinked recipes↔plants: {relink}")
    return {"points_indexed": len(points), "collection": QDRANT_PLANTS_COLLECTION, "relink": relink}


@activity.defn
async def index_activity(book_id: str) -> dict:
    bid = uuid.UUID(book_id)
    async with async_session() as db:
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()

        if (book.domain or "").lower() in ("herbalism", "fungi"):
            return await _index_plants(db, book)

        if (book.domain or "").lower() == "reference":
            # A reference (phytochemistry) book yields no recipes/plants of its
            # own — its product is the compound vocabulary + the corpus-wide
            # normalize, both already persisted in Postgres by the earlier steps.
            # The only thing left to index is the book's prose, so agents can
            # retrieve passages ("что Георгиевский пишет про алкалоиды").
            section_points = await _index_sections(db, book)
            book.wizard_step = 8
            book.status = "indexed"
            db.add(ProcessingLog(book_id=bid, step="index", status="completed",
                                 details={"section_points": section_points,
                                          "collection": "sections_v1", "domain": "reference"}))
            await db.commit()
            return {"section_points": section_points, "collection": "sections_v1"}

        recipe_points = await _index_recipes(db, book)
        if recipe_points == 0:
            raise ValueError("No recipes to index")

        # Also embed the book's free-text sections so agents can retrieve prose
        # passages (intro/appendix/other + recipe-block connective text).
        section_points = await _index_sections(db, book)
        if section_points:
            _hb(f"Indexed {section_points} section passage(s)")

        book.wizard_step = 8
        book.status = "indexed"
        db.add(ProcessingLog(book_id=bid, step="index", status="completed",
                             details={"points_indexed": recipe_points,
                                      "collection": QDRANT_COLLECTION,
                                      "section_points": section_points}))
        await db.commit()

    return {"points_indexed": recipe_points, "collection": QDRANT_COLLECTION,
            "section_points": section_points}


# ──────────────────────────────────────────────────────────────────────
# De-risk: trivial ping activity (connectivity smoke test)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def ping_activity(name: str) -> str:
    _hb(f"ping received: {name}")
    return f"pong: {name}"


# ──────────────────────────────────────────────────────────────────────
# Medical normalizer (corpus-wide maintenance, not a per-book step)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def medical_vocab_batch_activity(axis: str, batch_size: int = 80,
                                       offset: int = 0) -> dict:
    """One durable batch of Phase-A medical-vocab canonicalization for ``axis``
    ("actions" | "indications").

    Recomputes the UNCOVERED raw terms and canonicalizes the ``batch_size`` slice
    starting at ``offset`` via the LLM, upserting the concepts. The workflow walks
    ``offset`` across the whole backlog (a "sweep") rather than always re-serving
    the sorted head: an atom the model declines to map would otherwise sit at the
    head forever and block every mappable atom behind it. ``total`` is the uncovered
    count before this batch, so the workflow knows when a sweep is complete. Lets
    exceptions propagate so a provider 429 that outlasts the LLM client's own
    retries becomes a retriable Temporal attempt rather than a silently-dropped batch.
    """
    from app.services.medical_vocab import gather_uncovered, process_vocab_batch

    async with async_session() as db:
        uncovered = await gather_uncovered(db, axis)
        total = len(uncovered)
        if total == 0:
            _hb(f"medical vocab [{axis}]: nothing uncovered")
            return {"axis": axis, "processed": 0, "created": 0, "remaining": 0, "total": 0}
        if offset >= total:
            # the backlog shrank below this offset mid-sweep — nothing here to do
            _hb(f"medical vocab [{axis}]: offset {offset} past end ({total} uncovered)")
            return {"axis": axis, "processed": 0, "created": 0, "remaining": total, "total": total}
        batch = uncovered[offset:offset + batch_size]
        _hb(f"medical vocab [{axis}]: {total} uncovered, canonicalizing {len(batch)} @ offset {offset}")
        created = await process_vocab_batch(db, axis, batch)
        remaining = len(await gather_uncovered(db, axis))
        _hb(f"medical vocab [{axis}]: +{created} concepts, {remaining} still uncovered")
        return {"axis": axis, "processed": len(batch), "created": created,
                "remaining": remaining, "total": total}


@activity.defn
async def normalize_medical_activity() -> dict:
    """Phase B — idempotent corpus-wide recompute of every PlantMedicinalUse's
    ``action_id`` + ``indication_ids`` against the current vocabularies."""
    from app.services.medical_matching import normalize_medical_uses

    _hb("Normalizing medicinal uses against the action + indication vocabularies")
    async with async_session() as db:
        result = await normalize_medical_uses(db)
    _hb(f"Normalized medical uses: {result}")
    return result


# ──────────────────────────────────────────────────────────────────────
# klex.ru herbalism mirror (corpus acquisition, not a per-book step)
# ──────────────────────────────────────────────────────────────────────

@activity.defn
async def klex_list_activity() -> list:
    """Fetch klex.ru/razdel/herb and return [[code, title], ...] for every book."""
    from app.services import klex

    books = await klex.list_books()
    _hb(f"klex listing: {len(books)} books")
    return books


@activity.defn
async def klex_download_activity(code: str, bucket: str, prefix: str = "",
                                 formats: str = "") -> dict:
    """Mirror one klex.ru book into ``bucket``. Idempotent (skips if the object
    already exists) and heartbeats during the multi-MB streamed download, so a
    worker restart mid-file just retries this one book."""
    from app.services import klex

    res = await klex.download_book(
        code, bucket, prefix=prefix,
        formats=formats or klex.DEFAULT_FORMATS,
        heartbeat=_hb,
    )
    _hb(f"klex {code}: {res.get('status')} {res.get('key', '')}")
    return res
