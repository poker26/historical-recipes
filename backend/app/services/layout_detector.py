"""Layout detection: identify headers, body text, footnotes, margin notes."""

# TODO: implement
# Key functions:
# - detect_layout(text, page_image=None) -> list of zones
#   - Each zone: {type: 'header'|'body'|'footnote'|'margin_note', text: str}
# - classify_chunk(text) -> chunk_type
#   - 'recipe', 'intro', 'chapter_header', 'other'
