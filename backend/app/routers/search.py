from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.services import embedder, qdrant as qdrant_svc

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    collection: str | None = None  # recipes, herbalism, or None = both
    mode: str = "hybrid"  # hybrid, dense, sparse
    limit: int = 10


class SearchResult(BaseModel):
    id: str
    score: float
    collection: str
    payload: dict


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query: str
    mode: str
    collections_searched: list[str]


@router.post("/", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Hybrid search across Qdrant collections."""
    # 1. Create embeddings
    vectors = await embedder.create_embedding(request.query)
    dense = vectors.get("dense", [])
    sparse = vectors.get("sparse")

    # 2. Determine collections to search
    if request.collection:
        collections = [request.collection]
    else:
        collections = [
            settings.qdrant_collection_recipes,
            settings.qdrant_collection_herbalism,
        ]

    # 3. Search
    all_results = []

    for coll in collections:
        if request.mode == "dense" or coll == settings.qdrant_collection_herbalism:
            hits = await qdrant_svc.dense_search(coll, dense, limit=request.limit)
        elif request.mode == "sparse" and sparse:
            hits = await qdrant_svc.hybrid_search(
                coll, dense, sparse, limit=request.limit
            )
        else:
            hits = await qdrant_svc.hybrid_search(
                coll, dense, sparse, limit=request.limit
            )

        for hit in hits:
            all_results.append(SearchResult(
                id=str(hit.get("id", "")),
                score=hit.get("score", 0.0),
                collection=coll,
                payload=hit.get("payload", {}),
            ))

    # 4. Sort by score descending
    all_results.sort(key=lambda r: r.score, reverse=True)

    return SearchResponse(
        results=all_results[:request.limit],
        query=request.query,
        mode=request.mode,
        collections_searched=collections,
    )


@router.post("/test", response_model=SearchResponse)
async def search_test(request: SearchRequest):
    """Test search — same as search but intended for testing UI."""
    return await search(request)


@router.get("/collections")
async def list_collections():
    """List available Qdrant collections."""
    collections = await qdrant_svc.list_collections()
    return {"collections": collections}
