import uuid

from sqlalchemy import String, Integer, Text, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    name_latin: Mapped[str | None] = mapped_column(Text)
    names_historical: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    family: Mapped[str | None] = mapped_column(Text)
    parts_used: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(100))
    qdrant_collection: Mapped[str | None] = mapped_column(String(50))

    properties: Mapped[list["PlantProperty"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    mentions: Mapped[list["PlantBookMention"]] = relationship(back_populates="plant", cascade="all, delete-orphan")


class PlantProperty(Base):
    __tablename__ = "plant_properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    property_type: Mapped[str] = mapped_column(String(20))  # medicinal, flavor, aroma, color
    property: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))

    plant: Mapped["Plant"] = relationship(back_populates="properties")


class PlantCompatibility(Base):
    __tablename__ = "plant_compatibility"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    plant_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    compatibility: Mapped[str] = mapped_column(String(20))  # synergy, neutral, conflict
    context: Mapped[str | None] = mapped_column(String(30))  # вкус, лечебное действие, аромат
    description: Mapped[str | None] = mapped_column(Text)
    source_book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))


class PlantBookMention(Base):
    __tablename__ = "plant_book_mentions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("book_chunks.id", ondelete="SET NULL"))
    original_name: Mapped[str | None] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column()

    plant: Mapped["Plant"] = relationship(back_populates="mentions")
