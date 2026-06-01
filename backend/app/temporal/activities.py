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

from sqlalchemy import select, delete, func
from temporalio import activity

from app.database import async_session
from app.models.book import Book, BookPage, BookSection, ProcessingLog
from app.models.recipe import Recipe, RecipeIngredient
from app.models.ingredient import Ingredient, IngredientSynonym
from app.models.plant import (
    Plant, MedicinalAction, PlantMedicinalUse, PlantCompound,
    PlantHarvest, PlantHabitat, PlantToxicity, PlantBookMention,
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
from app.services.plant_matching import PlantMatcher, relink_recipe_ingredients

logger = logging.getLogger(__name__)

QDRANT_COLLECTION = "recipes_v2"
QDRANT_PLANTS_COLLECTION = "plants_v2"

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
# Herbalism branch: Extract Plant Monographs
# ──────────────────────────────────────────────────────────────────────

def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


async def _resolve_plant(db, ep) -> Plant:
    """Find an existing plant (by Latin name, else Russian name) or create one.

    Plants are shared across source books — a herbal enriches an existing plant
    rather than duplicating it. Identity fields are filled in only when missing
    so the first/most-complete source wins; ``parts_used`` is merged.
    """
    plant = None
    if ep.name_latin:
        plant = (await db.execute(
            select(Plant).where(func.lower(Plant.name_latin) == _norm(ep.name_latin))
        )).scalars().first()
    if plant is None and ep.name:
        plant = (await db.execute(
            select(Plant).where(func.lower(Plant.name) == _norm(ep.name))
        )).scalars().first()

    if plant is None:
        plant = Plant(name=ep.name, name_latin=ep.name_latin or None,
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


async def _save_plant_chunk(bid: uuid.UUID, chunk_index: int, plants: list, action_map: dict) -> tuple[int, int]:
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
            plant = await _resolve_plant(db, ep)
            for u in ep.medicinal_uses:
                db.add(PlantMedicinalUse(
                    plant_id=plant.id, part=u.part or None,
                    action_id=action_map.get(_norm(u.action)),
                    action_raw=u.action or None,
                    indications=u.indications or None,
                    preparation=u.preparation or None,
                    dosage=u.dosage or None,
                    contraindications=u.contraindications or None,
                    original_text=u.original_text or None,
                    source_book_id=bid,
                ))
                uses_count += 1
            for c in ep.compounds:
                db.add(PlantCompound(
                    plant_id=plant.id, compound=c.compound,
                    compound_group=c.compound_group or None,
                    part=c.part or None, notes=c.notes or None, source_book_id=bid,
                ))
            for h in ep.harvests:
                db.add(PlantHarvest(
                    plant_id=plant.id, part=h.part or None, season=h.season or None,
                    method=h.method or None, original_text=h.original_text or None,
                    source_book_id=bid,
                ))
            for hb in ep.habitats:
                db.add(PlantHabitat(
                    plant_id=plant.id, region=hb.region or None, biotope=hb.biotope or None,
                    status=hb.status or None, original_text=hb.original_text or None,
                    source_book_id=bid,
                ))
            for t in ep.toxicities:
                db.add(PlantToxicity(
                    plant_id=plant.id, toxic_parts=t.toxic_parts or None,
                    symptoms=t.symptoms or None, antidote=t.antidote or None,
                    severity=t.severity or None, original_text=t.original_text or None,
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

    Fresh vs. resume is keyed on ``activity.info().attempt``:
      * attempt 1  -> fresh run: drop this book's prior facts + stale chunk
                      markers, then process every chunk.
      * attempt >1 -> retry: keep committed chunks, skip them, do the rest.
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

    chunks = _split_into_chunks(full_text)
    n = len(chunks)
    _hb(f"Plant extraction: {n} chunk(s) from {len(full_text)} chars (attempt {attempt})")

    async with async_session() as db:
        if attempt == 1:
            # Fresh run: drop this book's prior contributions AND stale chunk
            # markers so everything is re-derived cleanly. Shared Plant rows and
            # other books' contributions are untouched.
            for tbl in (PlantMedicinalUse, PlantCompound, PlantHarvest, PlantHabitat, PlantToxicity):
                await db.execute(delete(tbl).where(tbl.source_book_id == bid))
            await db.execute(delete(PlantBookMention).where(PlantBookMention.book_id == bid))
            await db.execute(delete(ProcessingLog).where(
                ProcessingLog.book_id == bid, ProcessingLog.step == _PLANT_CHUNK_STEP))
            await db.commit()
            done_chunks: set[int] = set()
        else:
            logs = (await db.execute(select(ProcessingLog).where(
                ProcessingLog.book_id == bid,
                ProcessingLog.step == _PLANT_CHUNK_STEP,
            ))).scalars().all()
            done_chunks = {
                lg.details["chunk"] for lg in logs
                if lg.details and lg.details.get("chunk") is not None
            }

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
        pc, uc = await _save_plant_chunk(bid, i, plants, action_map)
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
        book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one()
        book.wizard_step = 6
        book.status = "plants_extracted"
        db.add(ProcessingLog(book_id=bid, step="extract_plant_entries", status="completed",
                             details={"plants_count": plants_count, "uses_count": uses_count,
                                      "failed_chunks": failed}))
        await db.commit()

    if plants_count == 0:
        raise ValueError("No plants extracted")
    return {"plants_count": plants_count, "uses_count": uses_count, "failed_chunks": failed}


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
            ing = ingredient_by_id.get(ingredient_id)
            names = [ri.name, ri.original_name]
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

        embed_text = (
            f"Растение: {plant.name}"
            + (f" ({plant.name_latin})" if plant.name_latin else "")
            + (f"\nДействие: {', '.join(actions)}" if actions else "")
            + (f"\nПрименяется при: {'; '.join(indications)}" if indications else "")
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

    book.wizard_step = 8
    book.status = "indexed"
    db.add(ProcessingLog(book_id=bid, step="index", status="completed",
                         details={"points_indexed": len(points), "collection": QDRANT_PLANTS_COLLECTION}))
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

        if (book.domain or "").lower() == "herbalism":
            return await _index_plants(db, book)

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
