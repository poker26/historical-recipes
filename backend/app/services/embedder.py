"""BGE-M3 embedding client (self-hosted). Produces dense + sparse vectors."""

import httpx

from app.config import settings


async def create_embedding(text: str) -> dict:
    """Create dense and sparse embeddings via BGE-M3.

    Args:
        text: Input text (max ~8192 tokens / ~16000 chars).

    Returns:
        Dict with 'dense' (list[float]) and 'sparse' (dict) vectors.
    """
    url = f"{settings.bge_m3_url}:{settings.bge_m3_timeout}/embed/qdrant"

    async with httpx.AsyncClient(timeout=settings.bge_m3_timeout) as client:
        response = await client.post(url, json={"text": text})
        response.raise_for_status()
        return response.json()


async def create_embeddings_batch(texts: list[str]) -> list[dict]:
    """Create embeddings for multiple texts."""
    url = f"{settings.bge_m3_url}:{settings.bge_m3_timeout}/embed/qdrant"

    async with httpx.AsyncClient(timeout=settings.bge_m3_timeout) as client:
        response = await client.post(url, json={"texts": texts})
        response.raise_for_status()
        return response.json()
