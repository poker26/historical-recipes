import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("book_chunks.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(30))  # водка, ликёр, настойка, бальзам, масло, вода
    original_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[str | None] = mapped_column(Text)
    # recipe triage (materialize_recipe_triage.py): is this a REAL, home-doable recipe, and what kind
    recipe_kind: Mapped[str | None] = mapped_column(String(20))   # medicinal|food|cosmetic|industrial|monograph|fragment|garbled|other
    is_recipe: Mapped[bool | None] = mapped_column(Boolean)
    home_doable: Mapped[bool | None] = mapped_column(Boolean)
    procedure_score: Mapped[int | None] = mapped_column(Integer)  # 0-2: qty + prep verb (2 = step-by-step)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(100))
    qdrant_collection: Mapped[str | None] = mapped_column(String(50))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    plant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plants.id", ondelete="SET NULL"))
    ingredient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingredients.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[str | None] = mapped_column(Text)
    amount_modern: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    unit_modern: Mapped[str | None] = mapped_column(String(50))

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")
