"""Chunked LLM text transformation (OCR cleanup, modernization).

A single full-book chat_completion truncates its output at max_tokens — for a
~1M-char book that silently drops ~94% of the text. This transforms the text in
line-based character chunks so each call's output stays within budget, then
rejoins. Failed or suspiciously-short (truncated) chunks fall back to the
original text so content is never lost.
"""

import asyncio
import logging

from app.services.llm import chat_completion

logger = logging.getLogger(__name__)

CHUNK_CHARS = 12_000        # input chars per call (~output fits in MAX_TOKENS)
MAX_CONCURRENT = 2          # keep low: free Qwen tier rate-limits when parallel
MAX_TOKENS = 16384


def _char_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of ~max_chars, breaking only on line boundaries."""
    lines = text.split("\n")
    chunks: list[str] = []
    cur: list[str] = []
    n = 0
    for ln in lines:
        if n + len(ln) + 1 > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, n = [], 0
        cur.append(ln)
        n += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


async def transform_text_chunked(
    text: str,
    system_prompt: str,
    task: str,
    cb=lambda m: None,
    chunk_chars: int = CHUNK_CHARS,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """Apply an LLM transformation (cleanup/translation) to long text in chunks."""
    chunks = _char_chunks(text, chunk_chars)
    total = len(chunks)
    cb(f"Transforming in {total} chunks (parallel x{MAX_CONCURRENT})")
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _one(idx: int, chunk: str) -> str:
        async with sem:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": chunk},
            ]
            try:
                out = await chat_completion(
                    messages, task=task, temperature=0.1, max_tokens=max_tokens,
                )
            except Exception as e:
                logger.error(f"Transform chunk {idx}/{total} failed: {e}; keeping original")
                cb(f"Chunk {idx}/{total}: ERROR {e} (kept original)")
                return chunk
            # Guard against truncation: a transform should return roughly as much
            # text as it received. Far less means it hit max_tokens — keep original.
            if len(out) < len(chunk) * 0.5:
                logger.warning(
                    f"Transform chunk {idx}/{total} suspiciously short "
                    f"({len(out)} vs {len(chunk)} chars); keeping original"
                )
                cb(f"Chunk {idx}/{total}: output too short ({len(out)}<{len(chunk)}), kept original")
                return chunk
            cb(f"Chunk {idx}/{total}: {len(chunk)} -> {len(out)} chars")
            return out

    results = await asyncio.gather(*[_one(i + 1, c) for i, c in enumerate(chunks)])
    return "\n".join(results)
