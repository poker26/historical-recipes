import uuid

from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DictionaryTerm(Base):
    __tablename__ = "dictionary_terms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(30))  # measurements, plants, orthography, techniques
    term_old: Mapped[str] = mapped_column(Text)
    term_modern: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
