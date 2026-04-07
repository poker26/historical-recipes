"""Qdrant client: indexing and hybrid search (RRF fusion)."""

# TODO: implement
# Key functions:
# - upsert_points(collection, points) -> None
# - delete_points(collection, point_ids) -> None
# - hybrid_search(collection, dense_vector, sparse_vector, limit=10) -> results
#   - Prefetch: dense(20) + sparse(20)
#   - RRF fusion -> top N
# - search_multi_collection(query_vectors, collections, limit) -> merged results
