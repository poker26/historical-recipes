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
MAX_CONCURRENT = 4          # paid OpenRouter tier; raised from 2 (the old free-tier cap). Watch logs for 429 before pushing higher.
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
    done_outputs: dict[int, str] | None = None,
    on_chunk_done=None,
) -> str:
    """Apply an LLM transformation (cleanup/translation) to long text in chunks.

    Resumable by the caller. Chunk boundaries are deterministic (line-based on a
    stable input), so chunk indices are reproducible across retries:

    * ``done_outputs`` — ``{1-based-idx: transformed_text}`` for chunks already
      persisted by an earlier attempt. Such chunks are returned verbatim, never
      re-sent to the LLM, so a retry doesn't re-spend tokens on completed work.
    * ``on_chunk_done(idx, out)`` — optional (async) hook invoked with each chunk's
      final text right after it is produced, so the caller (which owns the DB) can
      persist it before the activity might be torn down. Fallback (kept-original)
      chunks are persisted too, so they aren't endlessly retried.

    The service itself stays DB-free; persistence lives entirely in the hook.
    """
    chunks = _char_chunks(text, chunk_chars)
    total = len(chunks)
    done_outputs = done_outputs or {}
    cb(f"Transforming in {total} chunks (parallel x{MAX_CONCURRENT})")
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _one(idx: int, chunk: str) -> str:
        if idx in done_outputs:
            cb(f"Chunk {idx}/{total}: resumed (already done)")
            return done_outputs[idx]
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
                out = chunk
            else:
                # Guard against truncation: a transform should return roughly as
                # much text as it received. Far less means it hit max_tokens —
                # keep original.
                if len(out) < len(chunk) * 0.5:
                    logger.warning(
                        f"Transform chunk {idx}/{total} suspiciously short "
                        f"({len(out)} vs {len(chunk)} chars); keeping original"
                    )
                    cb(f"Chunk {idx}/{total}: output too short ({len(out)}<{len(chunk)}), kept original")
                    out = chunk
                else:
                    cb(f"Chunk {idx}/{total}: {len(chunk)} -> {len(out)} chars")
        # Persist this chunk's output before returning so a later interruption can
        # resume past it. Done outside the semaphore to not stall other chunks.
        if on_chunk_done is not None:
            await on_chunk_done(idx, out)
        return out

    results = await asyncio.gather(*[_one(i + 1, c) for i, c in enumerate(chunks)])
    return "\n".join(results)
