"""Add wizard models: book_sections, ingredients, ingredient_synonyms + book/chunk/recipe fields

Revision ID: 002_wizard
Revises: 001
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002_wizard"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new fields to books
    op.add_column("books", sa.Column("wizard_step", sa.Integer(), nullable=True, server_default="1"))
    op.add_column("books", sa.Column("full_text", sa.Text(), nullable=True))

    # Create book_sections table
    op.create_table(
        "book_sections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_type", sa.String(30), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("content_preview", sa.Text(), nullable=True),
        sa.Column("recipe_pattern", sa.Text(), nullable=True),
        sa.Column("estimated_recipe_count", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manually_verified", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Add section_id to book_chunks
    op.add_column("book_chunks", sa.Column("section_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_chunk_section", "book_chunks", "book_sections", ["section_id"], ["id"], ondelete="SET NULL")

    # Create ingredients table
    op.create_table(
        "ingredients",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False, unique=True),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create ingredient_synonyms table
    op.create_table(
        "ingredient_synonyms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ingredient_id", UUID(as_uuid=True), sa.ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("synonym", sa.Text(), nullable=False),
        sa.Column("language", sa.String(20), server_default="ru"),
        sa.Column("source_book_id", UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True),
    )

    # Add ingredient_id to recipe_ingredients
    op.add_column("recipe_ingredients", sa.Column("ingredient_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_recipe_ingredient_global", "recipe_ingredients", "ingredients", ["ingredient_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_recipe_ingredient_global", "recipe_ingredients", type_="foreignkey")
    op.drop_column("recipe_ingredients", "ingredient_id")
    op.drop_table("ingredient_synonyms")
    op.drop_table("ingredients")
    op.drop_constraint("fk_chunk_section", "book_chunks", type_="foreignkey")
    op.drop_column("book_chunks", "section_id")
    op.drop_table("book_sections")
    op.drop_column("books", "full_text")
    op.drop_column("books", "wizard_step")
