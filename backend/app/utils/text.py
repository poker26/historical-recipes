"""Text utilities: chunking, cleanup, token estimation."""

import re


def estimate_tokens(text: str) -> int:
    """Estimate token count for Russian text.

    Rough approximation: ~1 token per 0.75 words for Russian.
    BGE-M3 limit is 8192 tokens (~16000 chars).
    """
    words = len(text.split())
    return int(words / 0.75)


def split_into_token_chunks(text: str, max_tokens: int = 6000) -> list[str]:
    """Split text into chunks respecting token limit.

    Splits on paragraph boundaries (\n\n) when possible.
    """
    max_chars = int(max_tokens * 1.5)  # rough chars-to-tokens ratio for Russian

    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            # If a single paragraph exceeds limit, split by sentences
            if len(para) > max_chars:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sent in sentences:
                    if len(current) + len(sent) + 1 > max_chars:
                        if current:
                            chunks.append(current.strip())
                        current = sent
                    else:
                        current = f"{current} {sent}" if current else sent
            else:
                current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def clean_whitespace(text: str) -> str:
    """Normalize whitespace in text."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
