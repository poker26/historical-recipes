from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    collection: str | None = None  # recipes, herbalism, or both (None = both)
    mode: str = "hybrid"  # hybrid, dense, sparse
    limit: int = 10


@router.post("/")
async def search(request: SearchRequest):
    """Hybrid search across Qdrant collections (recipes + herbalism)."""
    # TODO: implement
    return {"results": []}


@router.post("/test")
async def search_test(request: SearchRequest):
    """Test search with quality metrics."""
    # TODO: implement
    return {"results": [], "metrics": {}}


@router.get("/collections")
async def list_collections():
    """List available Qdrant collections."""
    # TODO: implement
    return []
