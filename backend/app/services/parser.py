"""Recipe parser: extract structured recipes from text chunks.

Consolidates parsing logic from multiple n8n Code nodes.
"""

# TODO: implement (port from n8n workflows)
# Key functions:
# - parse_recipes(text, source_format=None) -> list[ParsedRecipe]
#   - Detect format: **, §, numbered, plain
#   - Extract: name, category, ingredients, process, year
# - parse_plants(text) -> list[ParsedPlant]
#   - For herbalism books: extract plant properties, uses, compatibility
