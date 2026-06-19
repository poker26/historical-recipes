from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database (Supabase self-hosted)
    database_url: str = "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"

    # MinIO
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "historical-recipes"
    # Dedicated bucket for field-uploaded identification photos (Android "Что
    # растёт" client): the original-ish photo + its EXIF/geo/device metadata are
    # archived here for debugging and future training data, kept apart from the
    # curated corpus bucket.
    minio_field_bucket: str = "field-uploads"
    minio_secure: bool = False

    # Qdrant (self-hosted)
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    # NOTE: search must query the SAME collection the indexer writes to.
    # Recipes are written to "recipes_v2" (the bare "recipes" name collides with
    # a foreign collection on the shared Qdrant). Plants are written to
    # "plants_v2" (see QDRANT_PLANTS_COLLECTION in temporal/activities.py); the
    # herbalism search default MUST match that or plant search 404s on a
    # non-existent "herbalism" collection and silently returns nothing.
    qdrant_collection_recipes: str = "recipes_v2"
    qdrant_collection_herbalism: str = "plants_v2"
    # Free-text book-section passages (intro/appendix/other + recipe-block prose);
    # written by _index_sections in temporal/activities.py.
    qdrant_collection_sections: str = "sections_v1"

    # BGE-M3 (self-hosted embeddings)
    bge_m3_url: str = "http://bge-m3:8100"
    bge_m3_port: int = 8100
    bge_m3_timeout: float = 60.0
    # The prod BGE-M3 endpoint is HTTPS with a self-signed cert on an internal
    # IP; set BGE_M3_VERIFY_SSL=false there so httpx doesn't reject the cert.
    bge_m3_verify_ssl: bool = True

    # OpenRouter (LLM gateway)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # LLM models per task
    llm_model_default: str = "qwen/qwen3-235b-a22b"
    # OCR vision models. Were Google Gemini, but Google ToS-blocks the book
    # content (alcohol distillates / medicinal preparations) with HTTP 403 on
    # the vision path too — same issue as the text tasks. Qwen3-VL handles
    # Cyrillic / pre-reform glyphs and doesn't ToS-filter.
    llm_model_ocr_fallback: str = "qwen/qwen3-vl-32b-instruct"
    llm_model_ocr_hard: str = "qwen/qwen3-vl-235b-a22b-instruct"
    llm_model_lightweight: str = "qwen/qwen3-32b"
    # Large-context model for text-only wizard tasks (structure analysis,
    # recipe extraction). Was google/gemini-2.5-pro, but Google ToS-blocks the
    # book content (alcohol distillates / medicinal preparations) with HTTP 403.
    # Qwen handles Russian well, has a 262K context, and doesn't ToS-filter.
    llm_model_long_context: str = "qwen/qwen3-235b-a22b-2507"

    # Pl@ntNet identification API (photo → species). Free tier = 500 ids/day; the
    # key is created at my.plantnet.org. Empty key disables the /api/identify route
    # gracefully (returns a clear error instead of calling the engine).
    plantnet_api_key: str = ""
    plantnet_base_url: str = "https://my-api.plantnet.org/v2"
    # Default project/flora list to identify against ("all" = world flora).
    plantnet_project: str = "all"
    # Optional HTTP(S) proxy for PlantNet calls. Prod server 1 has its egress to
    # PlantNet's host (CIRAD/RENATER, 193.51.117.x) blocked at the network level,
    # so the request is routed through the fleet's trusttunnel HTTPS proxy
    # (tt.begemot26.ru). Empty = direct connection.
    plantnet_proxy: str = ""

    # Kindwise Mushroom.id — the FUNGI identification engine (PlantNet is plants-only).
    # Paid API; key from kindwise.com. Empty key → the mushroom route returns a clear
    # error instead of calling the engine. Optional proxy mirrors PlantNet's egress fix.
    kindwise_api_key: str = ""
    kindwise_base_url: str = "https://mushroom.kindwise.com/api/v1"
    kindwise_proxy: str = ""

    # n8n
    n8n_base_url: str = "http://n8n:5678"
    n8n_webhook_secret: str = ""

    # Temporal (durable orchestration of the long-running book pipeline).
    # The shared cluster lives on server 3; gRPC frontend is plain (no TLS/auth).
    temporal_address: str = "45.12.72.157:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "historical-recipes"

    # Internal base URL of the FastAPI backend on the docker network. The MCP
    # service (separate container, reuses this image) calls the existing REST API
    # over this URL so the tool layer duplicates no SQL — all query logic stays
    # in the routers. Override via env on prod if the service name differs.
    internal_api_url: str = "http://backend:8000"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://frontend:3000"]

    # OCR
    tesseract_lang: str = "rus"
    ocr_confidence_auto: float = 80.0
    ocr_confidence_review: float = 60.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
