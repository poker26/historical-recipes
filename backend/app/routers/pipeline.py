"""Pipeline endpoints: atomic steps for n8n orchestration.

Each endpoint performs one processing step and returns the result.
n8n calls them sequentially to build the full book processing pipeline.
"""

import io
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.book import Book, BookPage, BookChunk, ProcessingLog
from app.services import minio as minio_svc
from app.services.preprocessor import split_pdf_to_pages, preprocess_page
from app.services.ocr import ocr_page, ocr_page_with_fallback
from app.services.postprocessor import clean_ocr_text, join_broken_words, detect_chunk_boundaries, split_into_chunks
from app.services.normalizer import normalize_orthography, full_normalize
from app.services.parser import parse_recipes

router = APIRouter()


async def _get_book(book_id: uuid.UUID, db: AsyncSession) -> Book:
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


async def _get_page(book_id: uuid.UUID, page_number: int, db: AsyncSession) -> BookPage:
    result = await db.execute(
        select(BookPage).where(BookPage.book_id == book_id, BookPage.page_number == page_number)
    )
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page {page_number} not found")
    return page


@router.post("/{book_id}/split")
async def split_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Split PDF into individual page images, save to MinIO."""
    book = await _get_book(book_id, db)

    if not book.file_path:
        raise HTTPException(status_code=400, detail="Book has no PDF file")

    pdf_bytes = minio_svc.download_file(book.file_path)
    pages = split_pdf_to_pages(pdf_bytes)

    # Delete existing pages for re-processing
    existing = await db.execute(select(BookPage).where(BookPage.book_id == book_id))
    for p in existing.scalars().all():
        await db.delete(p)
    await db.flush()

    page_list = []
    for i, (png_bytes, dpi) in enumerate(pages):
        page_num = i + 1
        image_path = f"books/{book_id}/pages/{page_num:04d}.png"
        minio_svc.upload_file(png_bytes, image_path, content_type="image/png")

        page = BookPage(
            book_id=book_id,
            page_number=page_num,
            image_path=image_path,
            dpi=dpi,
            status="split",
        )
        db.add(page)
        page_list.append({"page_number": page_num, "image_path": image_path, "dpi": dpi})

    book.status = "split"
    log = ProcessingLog(book_id=book_id, step="split", status="completed",
                        details={"total_pages": len(pages)})
    db.add(log)
    await db.commit()

    return {"status": "ok", "total_pages": len(pages), "pages": page_list}


@router.get("/{book_id}/pages/{page_number}/image")
async def get_page_image(book_id: uuid.UUID, page_number: int, preprocessed: bool = False,
                         db: AsyncSession = Depends(get_db)):
    """Return page image from MinIO. Use preprocessed=true for processed version."""
    page = await _get_page(book_id, page_number, db)

    image_path = page.preprocessed_path if preprocessed and page.preprocessed_path else page.image_path
    if not image_path:
        raise HTTPException(status_code=404, detail="Image not available")

    image_bytes = minio_svc.download_file(image_path)
    return Response(content=image_bytes, media_type="image/png")


@router.post("/{book_id}/pages/{page_number}/preprocess")
async def preprocess_page_endpoint(book_id: uuid.UUID, page_number: int,
                                   db: AsyncSession = Depends(get_db)):
    """Apply OpenCV preprocessing to a page image."""
    page = await _get_page(book_id, page_number, db)

    if not page.image_path:
        raise HTTPException(status_code=400, detail="Page has no image")

    image_bytes = minio_svc.download_file(page.image_path)
    processed_bytes = preprocess_page(image_bytes)

    preprocessed_path = f"books/{book_id}/pages/{page_number:04d}_preprocessed.png"
    minio_svc.upload_file(processed_bytes, preprocessed_path, content_type="image/png")

    page.preprocessed_path = preprocessed_path
    page.status = "preprocessed"
    await db.commit()

    return {"status": "ok", "page_number": page_number, "preprocessed_path": preprocessed_path}


@router.post("/{book_id}/pages/{page_number}/ocr")
async def ocr_page_endpoint(book_id: uuid.UUID, page_number: int,
                            use_llm_fallback: bool = True,
                            db: AsyncSession = Depends(get_db)):
    """Run OCR on a page. Uses preprocessed image if available."""
    page = await _get_page(book_id, page_number, db)

    image_path = page.preprocessed_path or page.image_path
    if not image_path:
        raise HTTPException(status_code=400, detail="No image available for OCR")

    image_bytes = minio_svc.download_file(image_path)

    if use_llm_fallback:
        text, confidence, method = await ocr_page_with_fallback(image_bytes)
    else:
        text, confidence = ocr_page(image_bytes)
        method = "tesseract"

    page.raw_text = text
    page.ocr_confidence = confidence
    page.needs_review = confidence < 60.0
    page.status = "ocr_done"
    await db.commit()

    return {
        "status": "ok",
        "page_number": page_number,
        "text_length": len(text),
        "confidence": round(confidence, 1),
        "method": method,
        "needs_review": page.needs_review,
    }


@router.post("/{book_id}/postprocess")
async def postprocess_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Clean OCR text for all pages, create chunks."""
    book = await _get_book(book_id, db)

    result = await db.execute(
        select(BookPage).where(BookPage.book_id == book_id).order_by(BookPage.page_number)
    )
    pages = result.scalars().all()
    if not pages:
        raise HTTPException(status_code=400, detail="No pages found")

    # Combine all page texts
    full_text = "\n\n".join(p.raw_text for p in pages if p.raw_text)
    if not full_text.strip():
        raise HTTPException(status_code=400, detail="No OCR text available")

    # Clean and join broken words
    cleaned = clean_ocr_text(full_text)
    cleaned = join_broken_words(cleaned)

    # Detect boundaries and split into chunks
    boundaries = detect_chunk_boundaries(cleaned)
    chunks = split_into_chunks(cleaned, boundaries)

    # Delete existing chunks for re-processing
    existing = await db.execute(select(BookChunk).where(BookChunk.book_id == book_id))
    for c in existing.scalars().all():
        await db.delete(c)
    await db.flush()

    chunk_list = []
    for i, chunk in enumerate(chunks):
        db_chunk = BookChunk(
            book_id=book_id,
            chunk_index=i,
            raw_text=chunk["text"],
            cleaned_text=chunk["text"],
            chunk_type="recipe" if chunk.get("pattern", "none") != "none" else "other",
            status="cleaned",
        )
        db.add(db_chunk)
        chunk_list.append({"index": i, "title": chunk.get("title", ""), "length": len(chunk["text"])})

    book.status = "postprocessed"
    log = ProcessingLog(book_id=book_id, step="postprocess", status="completed",
                        details={"chunks_created": len(chunks)})
    db.add(log)
    await db.commit()

    return {"status": "ok", "chunks_count": len(chunks), "chunks": chunk_list}


@router.post("/{book_id}/normalize")
async def normalize_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Normalize orthography and measures for all chunks."""
    book = await _get_book(book_id, db)

    result = await db.execute(
        select(BookChunk).where(BookChunk.book_id == book_id).order_by(BookChunk.chunk_index)
    )
    chunks = result.scalars().all()
    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks found")

    normalized_count = 0
    for chunk in chunks:
        if chunk.cleaned_text:
            chunk.normalized_text = await full_normalize(chunk.cleaned_text, db)
            chunk.status = "normalized"
            normalized_count += 1

    book.status = "normalized"
    log = ProcessingLog(book_id=book_id, step="normalize", status="completed",
                        details={"normalized_chunks": normalized_count})
    db.add(log)
    await db.commit()

    return {"status": "ok", "normalized_chunks": normalized_count}


@router.post("/{book_id}/parse")
async def parse_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Extract structured recipes from normalized chunks."""
    from app.models.recipe import Recipe, RecipeIngredient

    book = await _get_book(book_id, db)

    result = await db.execute(
        select(BookChunk).where(
            BookChunk.book_id == book_id, BookChunk.chunk_type == "recipe"
        ).order_by(BookChunk.chunk_index)
    )
    chunks = result.scalars().all()

    # Delete existing recipes for re-processing
    existing = await db.execute(select(Recipe).where(Recipe.book_id == book_id))
    for r in existing.scalars().all():
        await db.delete(r)
    await db.flush()

    recipes_created = []
    for chunk in chunks:
        text = chunk.normalized_text or chunk.cleaned_text or chunk.raw_text
        if not text:
            continue

        parsed = parse_recipes(text)
        for pr in parsed:
            recipe = Recipe(
                book_id=book_id,
                name=pr.name,
                category=pr.category,
                original_text=chunk.raw_text,
                normalized_text=chunk.normalized_text,
                qdrant_collection=book.domain,
            )
            db.add(recipe)
            await db.flush()

            for ing in pr.ingredients:
                db.add(RecipeIngredient(
                    recipe_id=recipe.id,
                    name=ing.name,
                    original_name=ing.original_name,
                    amount=ing.amount,
                    unit=ing.unit,
                ))

            recipes_created.append({"name": pr.name, "category": pr.category,
                                     "ingredients_count": len(pr.ingredients)})

    book.status = "parsed"
    log = ProcessingLog(book_id=book_id, step="parse", status="completed",
                        details={"recipes_count": len(recipes_created)})
    db.add(log)
    await db.commit()

    return {"status": "ok", "recipes_count": len(recipes_created), "recipes": recipes_created}
