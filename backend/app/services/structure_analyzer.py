"""LLM-based book structure analysis.

Sends the entire book text (or chunks) to an LLM and asks it to identify
sections: recipe blocks, bibliography, TOC, introductions, appendices, etc.
"""

import logging
from dataclasses import dataclass

from app.services.llm import chat_completion_json

logger = logging.getLogger(__name__)

MAX_CHARS_SINGLE_CALL = 400_000  # ~200K tokens — safe limit for Gemini 2.5 Pro
CHUNK_SIZE = 80_000  # ~40 pages per chunk (raw text, no line numbers)
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
    progress_callback=None,
) -> list[DetectedSection]:
    """Analyze book structure using LLM.

    Sends entire text if it fits, otherwise splits into overlapping chunks.
    Returns list of detected sections with types and boundaries.
    """
    cb = progress_callback or (lambda msg: None)
    text_len = len(text)
    lines_count = text.count("\n") + 1
    # Line numbers add ~7 chars per line ("12345: "), estimate inflated size
    estimated_with_numbers = text_len + lines_count * 7
    logger.info(f"Structure analysis: text={text_len} chars, lines={lines_count}, estimated_with_numbers={estimated_with_numbers}")
    cb(f"Text: {text_len} chars, {lines_count} lines")

    if estimated_with_numbers <= MAX_CHARS_SINGLE_CALL:
        logger.info("Using single-call mode (text fits in context)")
        cb("Single-call mode (text fits in LLM context)")
        return await _analyze_single(text, book_title, book_year, cb)
    else:
        logger.info(f"Using chunked mode (text too large, chunk_size={CHUNK_SIZE})")
        cb(f"Chunked mode (text too large for single call, chunk_size={CHUNK_SIZE})")
        return await _analyze_chunked(text, book_title, book_year, cb)


async def _analyze_single(
    text: str,
    book_title: str,
    book_year: int | None,
    cb=lambda msg: None,
) -> list[DetectedSection]:
    """Analyze entire book in a single LLM call."""
    numbered = _number_lines(text)
    year_str = f" ({book_year})" if book_year else ""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f'Here is the complete text of the book "{book_title}"{year_str}.\n\n{numbered}'},
    ]

    logger.info(f"Single-call: sending {len(numbered)} chars to LLM")
    cb(f"Sending {len(numbered)} chars to LLM (single call)")
    result = await chat_completion_json(
        messages,
        task="structure_analysis",
        temperature=0.1,
        max_tokens=16384,
    )

    sections = result if isinstance(result, list) else result.get("sections", [])
    logger.info(f"Single-call: got {len(sections)} sections")
    cb(f"LLM returned {len(sections)} sections")
    return [_parse_section(s) for s in sections]


async def _analyze_chunked(
    text: str,
    book_title: str,
    book_year: int | None,
    cb=lambda msg: None,
) -> list[DetectedSection]:
    """Analyze book in overlapping chunks, then merge results.

    Does NOT add line numbers to chunks (too much overhead).
    Instead, tells LLM the approximate line range.
    """
    lines = text.split("\n")
    total_lines = len(lines)
    all_sections = []

    # Build chunks by line count
    chunk_line_size = CHUNK_SIZE // 80  # ~80 chars per line average
    overlap_lines = CHUNK_OVERLAP // 80
    total_chunks = (total_lines + chunk_line_size - 1) // max(chunk_line_size - overlap_lines, 1)
    cb(f"Splitting into ~{total_chunks} chunks")
    chunk_num = 0

    start_line = 0
    while start_line < total_lines:
        end_line = min(start_line + chunk_line_size, total_lines)
        chunk_lines = lines[start_line:end_line]
        chunk_text = "\n".join(chunk_lines)
        chunk_num += 1

        year_str = f" ({book_year})" if book_year else ""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f'Here is a portion (lines {start_line+1}-{end_line} of {total_lines}) '
                f'of the book "{book_title}"{year_str}.\n'
                f'Use line numbers relative to the FULL book (this chunk starts at line {start_line+1}).\n\n'
                f'{chunk_text}'
            )},
        ]

        logger.info(f"Chunk {chunk_num}: lines {start_line+1}-{end_line}, {len(chunk_text)} chars")
        cb(f"Chunk {chunk_num}: lines {start_line+1}-{end_line}, sending {len(chunk_text)} chars to LLM")

        try:
            result = await chat_completion_json(
                messages,
                task="structure_analysis",
                temperature=0.1,
                max_tokens=16384,
            )

            sections = result if isinstance(result, list) else result.get("sections", [])
            logger.info(f"Chunk {chunk_num}: got {len(sections)} sections")
            cb(f"Chunk {chunk_num}: LLM returned {len(sections)} sections")
            all_sections.extend([_parse_section(s) for s in sections])
        except Exception as e:
            logger.error(f"Chunk {chunk_num} failed: {e}")
            cb(f"Chunk {chunk_num}: ERROR - {e}")
            # Continue with other chunks

        # Advance with overlap
        start_line = end_line - overlap_lines

    # Merge overlapping sections from different chunks
    merged = _merge_sections(all_sections)
    logger.info(f"Total: {len(all_sections)} raw sections -> {len(merged)} merged sections")
    cb(f"Merged: {len(all_sections)} raw -> {len(merged)} sections")
    return merged


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
    """Merge overlapping sections from chunked analysis."""
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
