import uuid

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.book import Book, BookPage, BookChunk, ProcessingLog
from app.models.recipe import Recipe
from app.schemas.book import (
    BookOut, BookDetailOut, BookPageOut, BookChunkOut, ChunkUpdate, ProcessingLogOut,
)
from app.services import minio as minio_svc
from app.services import qdrant as qdrant_svc
from app.services import ingest as ingest_svc

router = APIRouter()

QDRANT_COLLECTION = "recipes_v2"


@router.get("/", response_model=list[BookOut])
async def list_books(
    status: str | None = Query(None),
    domain: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List books with optional filters by status and domain."""
    query = select(Book).order_by(Book.created_at.desc())
    if status:
        query = query.where(Book.status == status)
    if domain:
        query = query.where(Book.domain == domain)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{book_id}", response_model=BookDetailOut)
async def get_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get book details with processing history and counts."""
    result = await db.execute(
        select(Book).options(selectinload(Book.logs)).where(Book.id == book_id)
    )
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Get counts
    pages_count = await db.scalar(
        select(func.count()).select_from(BookPage).where(BookPage.book_id == book_id)
    )
    chunks_count = await db.scalar(
        select(func.count()).select_from(BookChunk).where(BookChunk.book_id == book_id)
    )
    recipes_count = await db.scalar(
        select(func.count()).select_from(Recipe).where(Recipe.book_id == book_id)
    )

    return BookDetailOut(
        **BookOut.model_validate(book).model_dump(),
        pages_count=pages_count or 0,
        chunks_count=chunks_count or 0,
        recipes_count=recipes_count or 0,
        logs=[ProcessingLogOut.model_validate(log) for log in book.logs],
    )


@router.post("/upload", response_model=BookOut)
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(...),
    domain: str = Form("recipes"),
    language: str = Form("modern_ru"),
    author: str | None = Form(None),
    year: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a book (PDF / DjVu / TXT / DOCX) to MinIO and create a record.

    DjVu is converted to PDF on ingest so the rest of the pipeline only ever
    deals with PDFs.  Born-text formats (txt/docx) keep their text and skip OCR.
    """
    source_format = ingest_svc.detect_source_format(file.filename or "")
    if not source_format:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Accepted: .pdf, .djvu, .txt, .docx",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Normalise DjVu to PDF up front; downstream treats it as a PDF.
    if source_format == "djvu":
        try:
            content = ingest_svc.djvu_to_pdf(content)
        except Exception as e:  # noqa: BLE001 — surface conversion failure to the client
            raise HTTPException(status_code=400, detail=f"DjVu conversion failed: {e}")

    # Determine stored object + preliminary pdf_type.
    if source_format in ingest_svc.BORN_TEXT_FORMATS:
        stored_ext = source_format
        content_type = "text/plain" if source_format == "txt" else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        pdf_type = "text"
    else:  # pdf, or djvu now converted to pdf
        stored_ext = "pdf"
        content_type = "application/pdf"
        pdf_type = _detect_pdf_type(content)

    # Create book record first to get ID
    book = Book(
        title=title,
        domain=domain,
        language=language,
        author=author,
        year=year,
        pdf_type=pdf_type,
        source_format=source_format,
        status="uploaded",
    )
    db.add(book)
    await db.flush()

    # Upload to MinIO: books/{id}/original.{ext}
    file_path = f"books/{book.id}/original.{stored_ext}"
    minio_svc.upload_file(content, file_path, content_type=content_type)
    book.file_path = file_path

    # Log
    log = ProcessingLog(book_id=book.id, step="upload", status="completed",
                        details={"size": len(content), "pdf_type": pdf_type,
                                 "source_format": source_format})
    db.add(log)

    await db.commit()
    await db.refresh(book)
    return book


@router.patch("/{book_id}", response_model=BookOut)
async def update_book(
    book_id: uuid.UUID,
    title: str | None = None,
    author: str | None = None,
    year: int | None = None,
    domain: str | None = None,
    language: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Update book metadata."""
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    for field, value in [("title", title), ("author", author), ("year", year),
                         ("domain", domain), ("language", language), ("status", status)]:
        if value is not None:
            setattr(book, field, value)

    await db.commit()
    await db.refresh(book)
    return book


@router.delete("/{book_id}")
async def delete_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a book and its files from MinIO."""
    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    # Delete files from MinIO
    files = minio_svc.list_files(f"books/{book_id}/")
    for f in files:
        minio_svc.delete_file(f)

    # Delete this book's vectors from Qdrant (else they linger in search after
    # the Postgres rows are gone). Best-effort: don't block deletion on it.
    try:
        await qdrant_svc.delete_by_filter(QDRANT_COLLECTION, "book_id", str(book_id))
    except Exception:
        pass

    await db.delete(book)
    await db.commit()
    return {"status": "deleted"}


@router.post("/{book_id}/process")
async def process_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger the full processing pipeline via n8n webhook."""
    import httpx
    from app.config import settings

    result = await db.execute(select(Book).where(Book.id == book_id))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    book.status = "processing"
    log = ProcessingLog(book_id=book_id, step="pipeline", status="started")
    db.add(log)
    await db.commit()

    # Trigger n8n webhook (fire-and-forget)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{settings.n8n_base_url}/webhook/book-process",
                json={"book_id": str(book_id)},
            )
    except Exception:
        pass  # n8n may not be configured yet — don't block UI

    return {"status": "processing", "book_id": str(book_id)}


@router.get("/{book_id}/pages", response_model=list[BookPageOut])
async def list_pages(
    book_id: uuid.UUID,
    needs_review: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List pages with OCR confidence scores."""
    query = select(BookPage).where(BookPage.book_id == book_id).order_by(BookPage.page_number)
    if needs_review is not None:
        query = query.where(BookPage.needs_review == needs_review)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{book_id}/chunks", response_model=list[BookChunkOut])
async def list_chunks(
    book_id: uuid.UUID,
    chunk_type: str | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List recognized text chunks."""
    query = select(BookChunk).where(BookChunk.book_id == book_id).order_by(BookChunk.chunk_index)
    if chunk_type:
        query = query.where(BookChunk.chunk_type == chunk_type)
    if status:
        query = query.where(BookChunk.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/{book_id}/chunks/{chunk_id}", response_model=BookChunkOut)
async def update_chunk(
    book_id: uuid.UUID,
    chunk_id: uuid.UUID,
    body: ChunkUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Manually edit a chunk (normalized_text, chunk_type, etc)."""
    result = await db.execute(
        select(BookChunk).where(BookChunk.id == chunk_id, BookChunk.book_id == book_id)
    )
    chunk = result.scalar_one_or_none()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(chunk, field, value)

    await db.commit()
    await db.refresh(chunk)
    return chunk


@router.get("/{book_id}/logs", response_model=list[ProcessingLogOut])
async def list_logs(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get processing history for a book."""
    result = await db.execute(
        select(ProcessingLog)
        .where(ProcessingLog.book_id == book_id)
        .order_by(ProcessingLog.created_at.desc())
    )
    return result.scalars().all()


def _detect_pdf_type(content: bytes) -> str:
    """Detect if PDF contains text or only images."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        text_pages = 0
        total_pages = len(doc)
        for page in doc:
            text = page.get_text().strip()
            if len(text) > 50:
                text_pages += 1
        doc.close()

        if text_pages == 0:
            return "image"
        elif text_pages == total_pages:
            return "text"
        else:
            return "mixed"
    except Exception:
        return "unknown"
