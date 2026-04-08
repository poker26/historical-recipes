import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    category: Mapped[str | None] = mapped_column(String(30))  # plant, spice, liquid, mineral, other
    plant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plants.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    synonyms: Mapped[list["IngredientSynonym"]] = relationship(back_populates="ingredient", cascade="all, delete-orphan")


class IngredientSynonym(Base):
    __tablename__ = "ingredient_synonyms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredients.id", ondelete="CASCADE"))
    synonym: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="ru")
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    ingredient: Mapped["Ingredient"] = relationship(back_populates="synonyms")
