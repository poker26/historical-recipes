import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Float, Boolean, Text, DateTime, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(20), default="modern_ru")  # modern_ru, pre_reform_ru
    pdf_type: Mapped[str] = mapped_column(String(10), default="text")  # image, text, mixed
    domain: Mapped[str] = mapped_column(String(20), default="recipes")  # recipes, herbalism
    file_path: Mapped[str | None] = mapped_column(Text)  # path in MinIO
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    wizard_step: Mapped[int] = mapped_column(Integer, default=1)  # current wizard step 1-7
    full_text: Mapped[str | None] = mapped_column(Text)  # cleaned combined text — permanent asset
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pages: Mapped[list["BookPage"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    chunks: Mapped[list["BookChunk"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    sections: Mapped[list["BookSection"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    logs: Mapped[list["ProcessingLog"]] = relationship(back_populates="book", cascade="all, delete-orphan")


class BookPage(Base):
    __tablename__ = "book_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str | None] = mapped_column(Text)
    preprocessed_path: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    dpi: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")

    book: Mapped["Book"] = relationship(back_populates="pages")


class BookChunk(Base):
    __tablename__ = "book_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str | None] = mapped_column(Text)
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    chunk_type: Mapped[str | None] = mapped_column(String(30))  # recipe, intro, chapter_header, other
    section_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("book_sections.id", ondelete="SET NULL"))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    layout_zone: Mapped[str | None] = mapped_column(String(20))  # body, header, footnote, margin_note
    status: Mapped[str] = mapped_column(String(20), default="raw")

    book: Mapped["Book"] = relationship(back_populates="chunks")
    section: Mapped["BookSection | None"] = relationship(back_populates="chunks")


class BookSection(Base):
    __tablename__ = "book_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    section_type: Mapped[str] = mapped_column(String(30))  # recipe_block, bibliography, toc, introduction, appendix, chapter_header, other
    title: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    start_char: Mapped[int | None] = mapped_column(Integer)
    end_char: Mapped[int | None] = mapped_column(Integer)
    content_preview: Mapped[str | None] = mapped_column(Text)
    recipe_pattern: Mapped[str | None] = mapped_column(Text)  # formatting pattern for recipe_block
    estimated_recipe_count: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    manually_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    book: Mapped["Book"] = relationship(back_populates="sections")
    chunks: Mapped[list["BookChunk"]] = relationship(back_populates="section")


class ProcessingLog(Base):
    __tablename__ = "processing_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(30))  # ocr, normalize, parse, index
    status: Mapped[str] = mapped_column(String(20))  # started, completed, failed
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    book: Mapped["Book"] = relationship(back_populates="logs")
