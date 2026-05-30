"""BGE-M3 embedding client (self-hosted). Produces dense + sparse vectors.

Connects to self-hosted BGE-M3 service that returns both dense and sparse
vectors in Qdrant-compatible format via a single HTTP call.
"""

import httpx

from app.config import settings


async def create_embedding(text: str) -> dict:
    """Create dense and sparse embeddings via BGE-M3.

    Args:
        text: Input text (max ~8192 tokens / ~16000 chars for Russian).

    Returns:
        Dict with 'dense' (list[float], 1024d) and 'sparse' (dict with indices/values).
    """
    # Truncate to BGE-M3 limit
    if len(text) > 16000:
        text = text[:16000]

    url = f"{settings.bge_m3_url}/embed/qdrant"

    async with httpx.AsyncClient(
        timeout=settings.bge_m3_timeout,
        follow_redirects=True,
        verify=settings.bge_m3_verify_ssl,
    ) as client:
        response = await client.post(url, json={"texts": [text]})
        response.raise_for_status()
        data = response.json()
    return data["results"][0]


async def create_embeddings_batch(texts: list[str]) -> list[dict]:
    """Create embeddings for multiple texts in a single request.

    Args:
        texts: List of input texts.

    Returns:
        List of dicts, each with 'dense' and 'sparse' vectors.
    """
    truncated = [t[:16000] for t in texts]
    url = f"{settings.bge_m3_url}/embed/qdrant"

    async with httpx.AsyncClient(
        timeout=settings.bge_m3_timeout * 2,
        follow_redirects=True,
        verify=settings.bge_m3_verify_ssl,
    ) as client:
        response = await client.post(url, json={"texts": truncated})
        response.raise_for_status()
        data = response.json()
    return data["results"]
