"""LLM-based book structure analysis.

Sends the entire book text (or chunks) to an LLM and asks it to identify
sections: recipe blocks, bibliography, TOC, introductions, appendices, etc.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.services.llm import chat_completion_json

logger = logging.getLogger(__name__)

MAX_CHARS_SINGLE_CALL = 400_000  # ~200K tokens — safe limit for Gemini 2.5 Pro
CHUNK_SIZE = 150_000  # chars of book text per chunk (~75K tokens; Gemini handles 1M)
CHUNK_OVERLAP = 6_000  # chars of overlap between consecutive chunks
MAX_CONCURRENT_CHUNKS = 2  # parallel LLM calls (kept low: free Qwen tier rate-limits at 4)


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

Return a JSON object with a single key "sections" whose value is an ARRAY of \
section objects (one element per distinct section — do NOT return just one section). \
Each element of the "sections" array:
{
  "type": "recipe_block|bibliography|toc|introduction|appendix|chapter_header|other",
  "title": "section title or short description",
  "start_line": <first line number (1-based)>,
  "end_line": <last line number (1-based)>,
  "recipe_pattern": "pattern description (only for recipe_block, null otherwise)",
  "estimated_recipe_count": <number or null>,
  "confidence": 0.0-1.0
}

Example shape: {"sections": [ {"type": "introduction", ...}, {"type": "recipe_block", ...} ]}

Be thorough — identify every distinct section. Pay attention to:
- Multiple recipe blocks in one book (different chapters for vodkas, liqueurs, tinctures, etc.)
- Bibliography sections often at the end, with numbered author references
- Tables of contents often at the beginning or end
- Introductory text explaining methods, equipment, general principles"""


def _number_lines(text: str) -> str:
    """Add line numbers to text for LLM reference."""
    lines = text.split("\n")
    return "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines))


def _number_lines_range(lines: list[str], start_idx: int, end_idx: int) -> str:
    """Number a slice of lines using ABSOLUTE (full-book) 1-based line numbers."""
    return "\n".join(
        f"{start_idx + k + 1}: {line}" for k, line in enumerate(lines[start_idx:end_idx])
    )


def _build_char_chunks(lines: list[str], chunk_chars: int, overlap_chars: int) -> list[tuple[int, int]]:
    """Split lines into overlapping windows sized by ACTUAL character count.

    Returns a list of (start_idx, end_idx) line-index pairs (end exclusive).
    Avoids the old "~80 chars per line" guess that made chunks 3x too small.
    """
    n = len(lines)
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < n:
        size = 0
        end = start
        while end < n and size < chunk_chars:
            size += len(lines[end]) + 1  # +1 for the newline
            end += 1
        chunks.append((start, end))
        if end >= n:
            break
        # Step back by ~overlap_chars worth of lines for context continuity
        ov = 0
        j = end - 1
        while j > start and ov < overlap_chars:
            ov += len(lines[j]) + 1
            j -= 1
        start = max(start + 1, j + 1)
    return chunks


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

    sections = _extract_sections(result)
    logger.info(f"Single-call: got {len(sections)} sections")
    cb(f"LLM returned {len(sections)} sections")
    return [_parse_section(s) for s in sections]


async def _analyze_chunked(
    text: str,
    book_title: str,
    book_year: int | None,
    cb=lambda msg: None,
) -> list[DetectedSection]:
    """Analyze book in overlapping char-sized chunks, processed in parallel.

    Each chunk is line-numbered with ABSOLUTE full-book line numbers so the LLM
    can report exact section boundaries instead of guessing offsets.
    """
    lines = text.split("\n")
    total_lines = len(lines)

    chunks = _build_char_chunks(lines, CHUNK_SIZE, CHUNK_OVERLAP)
    total_chunks = len(chunks)
    cb(f"Splitting into {total_chunks} chunks (parallel x{MAX_CONCURRENT_CHUNKS})")

    year_str = f" ({book_year})" if book_year else ""
    sem = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)

    async def _process_chunk(idx: int, start_idx: int, end_idx: int) -> list[DetectedSection]:
        async with sem:
            numbered = _number_lines_range(lines, start_idx, end_idx)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f'Here is a portion (lines {start_idx+1}-{end_idx} of {total_lines}) '
                    f'of the book "{book_title}"{year_str}.\n'
                    f'Each line is prefixed with its absolute line number in the full book. '
                    f'Use those exact numbers for start_line/end_line.\n\n'
                    f'{numbered}'
                )},
            ]
            logger.info(f"Chunk {idx}/{total_chunks}: lines {start_idx+1}-{end_idx}, {len(numbered)} chars")
            cb(f"Chunk {idx}/{total_chunks}: lines {start_idx+1}-{end_idx}, sending {len(numbered)} chars to LLM")
            try:
                result = await chat_completion_json(
                    messages,
                    task="structure_analysis",
                    temperature=0.1,
                    max_tokens=32768,
                )
                sections = _extract_sections(result)
                logger.info(f"Chunk {idx}: got {len(sections)} sections")
                cb(f"Chunk {idx}/{total_chunks}: LLM returned {len(sections)} sections")
                return [_parse_section(s) for s in sections]
            except Exception as e:
                logger.error(f"Chunk {idx} failed: {e}")
                cb(f"Chunk {idx}/{total_chunks}: ERROR - {e}")
                return []  # continue with other chunks

    results = await asyncio.gather(*[
        _process_chunk(i + 1, s, e) for i, (s, e) in enumerate(chunks)
    ])
    all_sections = [sec for chunk_sections in results for sec in chunk_sections]

    # Merge overlapping sections from different chunks
    merged = _merge_sections(all_sections)
    logger.info(f"Total: {len(all_sections)} raw sections -> {len(merged)} merged sections")
    cb(f"Merged: {len(all_sections)} raw -> {len(merged)} sections")
    return merged


def _extract_sections(result) -> list[dict]:
    """Pull the list of section dicts out of an LLM JSON response.

    Models are inconsistent under json_object mode: some return a bare array,
    some wrap it as {"sections": [...]}, and some (e.g. Qwen) return a SINGLE
    section object. Normalize all of these to a list of dicts.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        # Wrapped array under a known/any key
        for key in ("sections", "result", "results", "data"):
            val = result.get(key)
            if isinstance(val, list):
                return val
        # A single section object returned directly (has section-ish keys)
        if any(k in result for k in ("type", "section_type", "start_line", "title")):
            return [result]
        # Fallback: first list-valued field, if any
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


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
