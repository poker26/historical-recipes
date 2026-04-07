"""Initial schema: books, pages, chunks, recipes, plants, dictionaries

Revision ID: 001
Revises: None
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === Books ===
    op.create_table(
        "books",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text()),
        sa.Column("year", sa.Integer()),
        sa.Column("language", sa.String(20), nullable=False, server_default="modern_ru"),
        sa.Column("pdf_type", sa.String(10), nullable=False, server_default="text"),
        sa.Column("domain", sa.String(20), nullable=False, server_default="recipes"),
        sa.Column("file_path", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("tags", ARRAY(sa.String())),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # === Book Pages ===
    op.create_table(
        "book_pages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text()),
        sa.Column("preprocessed_path", sa.Text()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("ocr_confidence", sa.Float()),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("dpi", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
    )
    op.create_index("ix_book_pages_book_id", "book_pages", ["book_id"])

    # === Book Chunks ===
    op.create_table(
        "book_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column("cleaned_text", sa.Text()),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("chunk_type", sa.String(30)),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("layout_zone", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="raw"),
    )
    op.create_index("ix_book_chunks_book_id", "book_chunks", ["book_id"])

    # === Processing Log ===
    op.create_table(
        "processing_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("details", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_processing_log_book_id", "processing_log", ["book_id"])

    # === Plants ===
    op.create_table(
        "plants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_latin", sa.Text()),
        sa.Column("names_historical", ARRAY(sa.String())),
        sa.Column("family", sa.Text()),
        sa.Column("parts_used", ARRAY(sa.String())),
        sa.Column("qdrant_point_id", sa.String(100)),
        sa.Column("qdrant_collection", sa.String(50)),
    )

    # === Plant Properties ===
    op.create_table(
        "plant_properties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("property_type", sa.String(20), nullable=False),
        sa.Column("property", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_plant_properties_plant_id", "plant_properties", ["plant_id"])

    # === Plant Compatibility ===
    op.create_table(
        "plant_compatibility",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_a_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_b_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("compatibility", sa.String(20), nullable=False),
        sa.Column("context", sa.String(30)),
        sa.Column("description", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )

    # === Plant Book Mentions ===
    op.create_table(
        "plant_book_mentions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", UUID(as_uuid=True), sa.ForeignKey("book_chunks.id", ondelete="SET NULL")),
        sa.Column("original_name", sa.Text()),
        sa.Column("original_text", sa.Text()),
        sa.Column("page_number", sa.Integer()),
    )
    op.create_index("ix_plant_book_mentions_plant_id", "plant_book_mentions", ["plant_id"])
    op.create_index("ix_plant_book_mentions_book_id", "plant_book_mentions", ["book_id"])

    # === Recipes ===
    op.create_table(
        "recipes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", UUID(as_uuid=True), sa.ForeignKey("book_chunks.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(30)),
        sa.Column("original_text", sa.Text()),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("year", sa.Integer()),
        sa.Column("quality", sa.Text()),
        sa.Column("qdrant_point_id", sa.String(100)),
        sa.Column("qdrant_collection", sa.String(50)),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_recipes_book_id", "recipes", ["book_id"])
    op.create_index("ix_recipes_category", "recipes", ["category"])

    # === Recipe Ingredients ===
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("recipe_id", UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text()),
        sa.Column("amount", sa.Text()),
        sa.Column("amount_modern", sa.Text()),
        sa.Column("unit", sa.String(50)),
        sa.Column("unit_modern", sa.String(50)),
    )
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"])
    op.create_index("ix_recipe_ingredients_plant_id", "recipe_ingredients", ["plant_id"])

    # === Dictionary Terms ===
    op.create_table(
        "dictionary_terms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("term_old", sa.Text(), nullable=False),
        sa.Column("term_modern", sa.Text(), nullable=False),
        sa.Column("context", sa.Text()),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL")),
    )
    op.create_index("ix_dictionary_terms_category", "dictionary_terms", ["category"])
    op.create_index("ix_dictionary_terms_term_old", "dictionary_terms", ["term_old"])


def downgrade() -> None:
    op.drop_table("dictionary_terms")
    op.drop_table("recipe_ingredients")
    op.drop_table("recipes")
    op.drop_table("plant_book_mentions")
    op.drop_table("plant_compatibility")
    op.drop_table("plant_properties")
    op.drop_table("plants")
    op.drop_table("processing_log")
    op.drop_table("book_chunks")
    op.drop_table("book_pages")
    op.drop_table("books")
