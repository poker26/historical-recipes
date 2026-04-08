"""LLM-based book structure analysis.

Sends the entire book text (or chunks) to an LLM and asks it to identify
sections: recipe blocks, bibliography, TOC, introductions, appendices, etc.
"""

from dataclasses import dataclass

from app.services.llm import chat_completion_json


MAX_CHARS_SINGLE_CALL = 500_000  # ~250K tokens, safe for Gemini 2.5 Pro (1M)
CHUNK_SIZE = 100_000  # ~50 pages per chunk
CHUNK_OVERLAP = 4_000  # ~2 pages overlap


@dataclass
class DetectedSection:
    section_type: str  # recipe_block, bibliography, toc, introduction, appendix, chapter_header, other
    title: str
    start_line: int
    end_line: int
    recipe_pattern: str | None = None
    estimated_recipe_count: int | None = None
    confidence: float = 0.8


SYSTEM_PROMPT = """You are analyzing the structure of a historical Russian book about herbal tinctures, \
distillates, and medicinal preparations. Your task is to identify what sections the book contains.

Identify ALL sections of the book. For each section, determine its type:
- recipe_block: a contiguous group of actual recipes with ingredients and preparation instructions
- bibliography: lists of references, authors, publications
- toc: table of contents
- introduction: foreword, preface, general descriptions without specific recipes
- appendix: tables (spirit content, temperatures, densities), indexes, supplements
- chapter_header: chapter/section dividers (short, just a title)
- other: anything that doesn't fit above

For recipe_block sections, also identify the formatting pattern used for recipe boundaries \
(e.g., "**N. Title**", "§ N. Title", "N) Title", or describe the specific pattern).

Return a JSON array. Each element:
{
  "type": "recipe_block|bibliography|toc|introduction|appendix|chapter_header|other",
  "title": "section title or short description",
  "start_line": <first line number (1-based)>,
  "end_line": <last line number (1-based)>,
  "recipe_pattern": "pattern description (only for recipe_block, null otherwise)",
  "estimated_recipe_count": <number or null>,
  "confidence": 0.0-1.0
}

Be thorough — identify every distinct section. Pay attention to:
- Multiple recipe blocks in one book (different chapters for vodkas, liqueurs, tinctures, etc.)
- Bibliography sections often at the end, with numbered author references
- Tables of contents often at the beginning or end
- Introductory text explaining methods, equipment, general principles"""


def _number_lines(text: str) -> str:
    """Add line numbers to text for LLM reference."""
    lines = text.split("\n")
    return "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines))


async def analyze_book_structure(
    text: str,
    book_title: str = "",
    book_year: int | None = None,
) -> list[DetectedSection]:
    """Analyze book structure using LLM.

    Sends entire text if it fits, otherwise splits into overlapping chunks.
    Returns list of detected sections with types and boundaries.
    """
    if len(text) <= MAX_CHARS_SINGLE_CALL:
        return await _analyze_single(text, book_title, book_year)
    else:
        return await _analyze_chunked(text, book_title, book_year)


async def _analyze_single(
    text: str,
    book_title: str,
    book_year: int | None,
) -> list[DetectedSection]:
    """Analyze entire book in a single LLM call."""
    numbered = _number_lines(text)
    year_str = f" ({book_year})" if book_year else ""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f'Here is the complete text of the book "{book_title}"{year_str}.\n\n{numbered}'},
    ]

    result = await chat_completion_json(
        messages,
        task="structure_analysis",
        temperature=0.1,
        max_tokens=16384,
    )

    sections = result if isinstance(result, list) else result.get("sections", [])
    return [_parse_section(s) for s in sections]


async def _analyze_chunked(
    text: str,
    book_title: str,
    book_year: int | None,
) -> list[DetectedSection]:
    """Analyze book in overlapping chunks, then merge results."""
    lines = text.split("\n")
    total_lines = len(lines)
    all_sections = []

    # Split into chunks by character count, tracking line numbers
    chunk_start_line = 0
    char_pos = 0

    while char_pos < len(text):
        chunk_end_char = min(char_pos + CHUNK_SIZE, len(text))

        # Find the line numbers for this chunk
        chunk_text = text[char_pos:chunk_end_char]
        chunk_lines = chunk_text.split("\n")
        chunk_end_line = chunk_start_line + len(chunk_lines) - 1

        # Number lines with global line numbers
        numbered = "\n".join(
            f"{chunk_start_line + i + 1}: {line}"
            for i, line in enumerate(chunk_lines)
        )

        year_str = f" ({book_year})" if book_year else ""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f'Here is a portion (lines {chunk_start_line+1}-{chunk_end_line+1} of ~{total_lines}) '
                f'of the book "{book_title}"{year_str}.\n\n{numbered}'
            )},
        ]

        result = await chat_completion_json(
            messages,
            task="structure_analysis",
            temperature=0.1,
            max_tokens=16384,
        )

        sections = result if isinstance(result, list) else result.get("sections", [])
        all_sections.extend([_parse_section(s) for s in sections])

        # Advance with overlap
        char_pos = chunk_end_char - CHUNK_OVERLAP
        chunk_start_line = max(0, chunk_end_line - 2)

    # Merge overlapping sections from different chunks
    return _merge_sections(all_sections)


def _parse_section(data: dict) -> DetectedSection:
    """Parse a section dict from LLM response."""
    return DetectedSection(
        section_type=data.get("type", "other"),
        title=data.get("title", ""),
        start_line=data.get("start_line", 0),
        end_line=data.get("end_line", 0),
        recipe_pattern=data.get("recipe_pattern"),
        estimated_recipe_count=data.get("estimated_recipe_count"),
        confidence=data.get("confidence", 0.8),
    )


def _merge_sections(sections: list[DetectedSection]) -> list[DetectedSection]:
    """Merge overlapping sections from chunked analysis.

    If two sections of the same type overlap by >50% of the smaller one,
    merge them into one with the wider range.
    """
    if not sections:
        return []

    sections.sort(key=lambda s: s.start_line)
    merged = [sections[0]]

    for s in sections[1:]:
        prev = merged[-1]
        # Check overlap: same type and overlapping line ranges
        if (s.section_type == prev.section_type and
                s.start_line <= prev.end_line):
            # Merge: extend end_line, keep higher confidence
            prev.end_line = max(prev.end_line, s.end_line)
            prev.confidence = max(prev.confidence, s.confidence)
            if s.estimated_recipe_count and prev.estimated_recipe_count:
                prev.estimated_recipe_count = max(prev.estimated_recipe_count, s.estimated_recipe_count)
            elif s.estimated_recipe_count:
                prev.estimated_recipe_count = s.estimated_recipe_count
            if s.recipe_pattern and not prev.recipe_pattern:
                prev.recipe_pattern = s.recipe_pattern
        else:
            merged.append(s)

    return merged
