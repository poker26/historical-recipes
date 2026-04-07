import uuid

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_books(
    status: str | None = None,
    domain: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List books with optional filters by status and domain."""
    # TODO: implement
    return []


@router.get("/{book_id}")
async def get_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get book details with processing history."""
    # TODO: implement
    return {}


@router.post("/upload")
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(...),
    domain: str = Form("recipes"),
    language: str = Form("modern_ru"),
    author: str | None = Form(None),
    year: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a PDF book to MinIO and create a book record."""
    # TODO: implement
    return {}


@router.post("/{book_id}/process")
async def process_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger the full processing pipeline via n8n webhook."""
    # TODO: implement
    return {}


@router.post("/{book_id}/preprocess")
async def preprocess_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Run pre-OCR image processing (binarize, deskew, denoise)."""
    # TODO: implement
    return {}


@router.get("/{book_id}/pages")
async def list_pages(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List pages with OCR confidence scores."""
    # TODO: implement
    return []


@router.get("/{book_id}/chunks")
async def list_chunks(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List recognized text chunks."""
    # TODO: implement
    return []


@router.patch("/{book_id}/chunks/{chunk_id}")
async def update_chunk(
    book_id: uuid.UUID,
    chunk_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manually edit a chunk (normalized_text, chunk_type, etc)."""
    # TODO: implement
    return {}
