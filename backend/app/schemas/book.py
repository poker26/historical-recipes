import uuid
from datetime import datetime

from pydantic import BaseModel


class BookCreate(BaseModel):
    title: str
    domain: str = "recipes"
    language: str = "modern_ru"
    author: str | None = None
    year: int | None = None
    tags: list[str] | None = None


class BookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    year: int | None = None
    language: str | None = None
    domain: str | None = None
    tags: list[str] | None = None
    status: str | None = None


class BookOut(BaseModel):
    id: uuid.UUID
    title: str
    author: str | None
    year: int | None
    language: str
    pdf_type: str
    domain: str
    file_path: str | None
    status: str
    tags: list[str] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BookPageOut(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID
    page_number: int
    image_path: str | None
    preprocessed_path: str | None
    raw_text: str | None
    ocr_confidence: float | None
    needs_review: bool
    dpi: int | None
    status: str

    model_config = {"from_attributes": True}


class BookChunkOut(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID
    chunk_index: int
    raw_text: str | None
    cleaned_text: str | None
    normalized_text: str | None
    chunk_type: str | None
    page_start: int | None
    page_end: int | None
    layout_zone: str | None
    status: str

    model_config = {"from_attributes": True}


class ChunkUpdate(BaseModel):
    cleaned_text: str | None = None
    normalized_text: str | None = None
    chunk_type: str | None = None
    layout_zone: str | None = None
    status: str | None = None


class ProcessingLogOut(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID
    step: str
    status: str
    details: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BookDetailOut(BookOut):
    pages_count: int = 0
    chunks_count: int = 0
    recipes_count: int = 0
    logs: list[ProcessingLogOut] = []
