from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database (Supabase self-hosted)
    database_url: str = "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"

    # MinIO
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "historical-recipes"
    minio_secure: bool = False

    # Qdrant (self-hosted)
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection_recipes: str = "recipes"
    qdrant_collection_herbalism: str = "herbalism"

    # BGE-M3 (self-hosted embeddings)
    bge_m3_url: str = "http://bge-m3:8100"
    bge_m3_timeout: float = 60.0

    # OpenRouter (LLM gateway)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # LLM models per task
    llm_model_default: str = "qwen/qwen3-235b-a22b"
    llm_model_ocr_fallback: str = "google/gemini-2.5-flash"
    llm_model_ocr_hard: str = "google/gemini-2.5-pro"
    llm_model_lightweight: str = "qwen/qwen3-32b"

    # n8n
    n8n_base_url: str = "http://n8n:5678"
    n8n_webhook_secret: str = ""

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://frontend:3000"]

    # OCR
    tesseract_lang: str = "rus"
    ocr_confidence_auto: float = 80.0
    ocr_confidence_review: float = 60.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
