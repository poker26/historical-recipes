"""Post-OCR text cleanup: artifact removal, word joining, chunk boundary detection.

Handles common Tesseract artifacts on historical Russian texts:
- Broken words ("ощ ущ аю тся" -> "ощущается")
- Stray characters (|, excessive newlines)
- Control characters
"""

import re


def clean_ocr_text(raw_text: str) -> str:
    """Full post-OCR cleanup pipeline.

    Returns cleaned text preserving paragraph structure.
    """
    text = raw_text

    # 1. Remove control characters (keep \n and \t)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 2. Remove common OCR artifacts
    text = text.replace("|", "")
    text = re.sub(r"[«»""„]", '"', text)

    # 3. Fix broken words (spaces inside Cyrillic words)
    # Pattern: single Cyrillic letter(s) separated by spaces that form a word
    text = _join_broken_words(text)

    # 4. Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)  # collapse horizontal whitespace
    text = re.sub(r" *\n *", "\n", text)  # trim spaces around newlines
    text = re.sub(r"\n{3,}", "\n\n", text)  # max 2 consecutive newlines

    # 5. Rejoin hyphenated line breaks (common in old typesetting)
    # "настой-\nка" -> "настойка"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    return text.strip()


def _join_broken_words(text: str) -> str:
    """Join Cyrillic characters that were broken apart by OCR.

    Detects patterns like "п р и г о т о в л е н i е" and joins them.
    Only joins if most "words" are 1-2 chars (indicates broken word).
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        words = line.split()
        if len(words) > 3:
            short_words = sum(1 for w in words if len(w) <= 2 and re.match(r"^[а-яА-ЯёЁѣiѳ]+$", w))
            if short_words > len(words) * 0.6:
                # Most "words" are 1-2 chars — likely a broken word
                line = re.sub(r"(?<=[а-яА-ЯёЁѣiѳ]) (?=[а-яА-ЯёЁѣiѳ])", "", line)
        result.append(line)
    return "\n".join(result)


def detect_chunk_boundaries(text: str) -> list[dict]:
    """Detect recipe/section boundaries in text.

    Looks for common patterns in historical recipe books:
    - **number. Title** (e.g., **42. Анисовая водка**)
    - § number (e.g., § 441)
    - №/No/N. number
    - Numbered patterns (1., 2., etc.) at line start with capital letters

    Returns list of {start: int, end: int, title: str, pattern: str}
    """
    boundaries = []

    # Pattern 1: **number. Title**
    for m in re.finditer(r"^\*\*(\d+)[.)]\s*(.+?)\*\*", text, re.MULTILINE):
        boundaries.append({
            "start": m.start(),
            "title": m.group(2).strip(),
            "number": int(m.group(1)),
            "pattern": "bold_numbered",
        })

    # Pattern 2: § number. Title
    for m in re.finditer(r"^§\s*(\d+)[.)]\s*(.+?)$", text, re.MULTILINE):
        boundaries.append({
            "start": m.start(),
            "title": m.group(2).strip(),
            "number": int(m.group(1)),
            "pattern": "section_sign",
        })

    # Pattern 3: №/No number. Title
    for m in re.finditer(r"^(?:№|No\.?|N\.?)\s*(\d+)[.)]\s*(.+?)$", text, re.MULTILINE):
        boundaries.append({
            "start": m.start(),
            "title": m.group(2).strip(),
            "number": int(m.group(1)),
            "pattern": "numero_sign",
        })

    # Pattern 4: Standalone number at line start with title
    if not boundaries:
        for m in re.finditer(r"^(\d+)[.)]\s+([А-ЯЁ][а-яёА-ЯЁ\s]{3,50})", text, re.MULTILINE):
            boundaries.append({
                "start": m.start(),
                "title": m.group(2).strip(),
                "number": int(m.group(1)),
                "pattern": "plain_numbered",
            })

    # Sort by position and compute end positions
    boundaries.sort(key=lambda b: b["start"])
    for i in range(len(boundaries)):
        if i + 1 < len(boundaries):
            boundaries[i]["end"] = boundaries[i + 1]["start"]
        else:
            boundaries[i]["end"] = len(text)

    return boundaries


def split_into_chunks(text: str, boundaries: list[dict]) -> list[dict]:
    """Split text into chunks based on detected boundaries.

    Returns list of {text: str, title: str, number: int, pattern: str}
    """
    if not boundaries:
        # No boundaries detected — return as single chunk
        return [{"text": text, "title": "", "number": 0, "pattern": "none"}]

    chunks = []
    for b in boundaries:
        chunk_text = text[b["start"]:b["end"]].strip()
        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "title": b["title"],
                "number": b["number"],
                "pattern": b["pattern"],
            })
    return chunks
