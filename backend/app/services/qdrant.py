"""Qdrant client: indexing and hybrid search with RRF fusion.

Supports two collections: 'recipes' (hybrid) and 'herbalism' (dense).
Uses Reciprocal Rank Fusion to combine dense and sparse search results.
"""

import uuid

import httpx

from app.config import settings


async def _request(method: str, path: str, json: dict | None = None) -> dict:
    """Make HTTP request to Qdrant."""
    url = f"{settings.qdrant_url}{path}"
    headers = {}
    if settings.qdrant_api_key:
        headers["api-key"] = settings.qdrant_api_key
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, json=json, headers=headers)
        response.raise_for_status()
        return response.json()


# === Collection management ===

async def list_collections() -> list[str]:
    """List all Qdrant collections."""
    data = await _request("GET", "/collections")
    return [c["name"] for c in data.get("result", {}).get("collections", [])]


async def ensure_collection(name: str, vector_size: int = 1024) -> None:
    """Create collection if it doesn't exist.

    Configures dual vectors: dense (cosine, 1024d) + sparse (IDF modifier).
    """
    collections = await list_collections()
    if name in collections:
        return

    await _request("PUT", f"/collections/{name}", json={
        "vectors": {
            "dense": {
                "size": vector_size,
                "distance": "Cosine",
            }
        },
        "sparse_vectors": {
            "sparse": {
                "modifier": "idf",
            }
        },
    })

    # Create payload indexes for filtering
    for field in ["category", "source_book", "recipe_name"]:
        await _request("PUT", f"/collections/{name}/index", json={
            "field_name": field,
            "field_schema": "keyword",
        })


# === Indexing ===

async def upsert_points(collection: str, points: list[dict]) -> None:
    """Upsert points into Qdrant.

    Each point: {id: str, dense: list[float], sparse: {indices, values}, payload: dict}
    """
    qdrant_points = []
    for p in points:
        point = {
            "id": p.get("id", str(uuid.uuid4())),
            "payload": p.get("payload", {}),
            "vector": {
                "dense": p["dense"],
            },
        }
        if "sparse" in p and p["sparse"]:
            point["vector"]["sparse"] = p["sparse"]
        qdrant_points.append(point)

    await _request("PUT", f"/collections/{collection}/points", json={
        "points": qdrant_points,
    })


async def delete_points(collection: str, point_ids: list[str]) -> None:
    """Delete points by IDs."""
    await _request("POST", f"/collections/{collection}/points/delete", json={
        "points": point_ids,
    })


async def delete_by_filter(collection: str, field: str, value: str) -> None:
    """Delete points matching a payload filter."""
    await _request("POST", f"/collections/{collection}/points/delete", json={
        "filter": {
            "must": [{"key": field, "match": {"value": value}}]
        },
    })


async def scroll_point_ids(collection: str, field: str, value: str) -> set[str]:
    """Return the IDs of all points matching a payload filter.

    Used by the indexer to resume after a transient failure: on a retried
    activity attempt we skip re-embedding points already written by the prior
    attempt. Pages through the scroll API; payload/vectors are excluded so this
    stays cheap even for a 1000+ point book.
    """
    ids: set[str] = set()
    offset = None
    while True:
        body: dict = {
            "filter": {"must": [{"key": field, "match": {"value": value}}]},
            "limit": 1000,
            "with_payload": False,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = (await _request(
            "POST", f"/collections/{collection}/points/scroll", json=body
        )).get("result", {})
        for p in result.get("points", []):
            ids.add(str(p["id"]))
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return ids


async def scroll_all_point_ids(collection: str) -> set[str]:
    """Return EVERY point id in a collection (no filter). Used by the data-quality
    linter to diff Postgres↔Qdrant for orphan/missing points. Payload/vectors
    excluded → cheap id-only paging even for tens of thousands of points."""
    ids: set[str] = set()
    offset = None
    while True:
        body: dict = {"limit": 1000, "with_payload": False, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = (await _request(
            "POST", f"/collections/{collection}/points/scroll", json=body
        )).get("result", {})
        for p in result.get("points", []):
            ids.add(str(p["id"]))
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return ids


# === Search ===

async def hybrid_search(
    collection: str,
    dense_vector: list[float],
    sparse_vector: dict | None = None,
    limit: int = 10,
    prefetch_limit: int = 20,
) -> list[dict]:
    """Hybrid search using RRF (Reciprocal Rank Fusion).

    Combines dense (semantic) and sparse (keyword) search results.
    """
    prefetch = [
        {
            "query": dense_vector,
            "using": "dense",
            "limit": prefetch_limit,
        }
    ]

    if sparse_vector:
        prefetch.append({
            "query": {
                "indices": sparse_vector["indices"],
                "values": sparse_vector["values"],
            },
            "using": "sparse",
            "limit": prefetch_limit,
        })

    body = {
        "prefetch": prefetch,
        "query": {"fusion": "rrf"},
        "limit": limit,
        "with_payload": True,
    }

    data = await _request("POST", f"/collections/{collection}/points/query", json=body)
    return data.get("result", {}).get("points", [])


async def dense_search(
    collection: str,
    dense_vector: list[float],
    limit: int = 10,
) -> list[dict]:
    """Dense-only search (for herbalism collection)."""
    body = {
        "query": dense_vector,
        "using": "dense",
        "limit": limit,
        "with_payload": True,
    }

    data = await _request("POST", f"/collections/{collection}/points/query", json=body)
    return data.get("result", {}).get("points", [])


async def search_multi_collection(
    dense_vector: list[float],
    sparse_vector: dict | None = None,
    collections: list[str] | None = None,
    limit: int = 10,
) -> dict[str, list[dict]]:
    """Search across multiple collections, returning results per collection."""
    if collections is None:
        collections = [
            settings.qdrant_collection_recipes,
            settings.qdrant_collection_herbalism,
        ]

    results = {}
    for coll in collections:
        if coll == settings.qdrant_collection_herbalism:
            # Herbalism uses dense-only search
            results[coll] = await dense_search(coll, dense_vector, limit=limit)
        else:
            # Recipes use hybrid search
            results[coll] = await hybrid_search(
                coll, dense_vector, sparse_vector, limit=limit
            )

    return results
