from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://ragcms:ragcms@localhost:5432/ragcms"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    data_dir: Path = Path("./data")
    max_upload_mb: int = 200

    voyage_api_key: str | None = None
    voyage_embed_model: str = "voyage-3"
    voyage_embed_dim: int = 1024

    # Embedding backend dispatch. "voyage" (default) keeps the existing Voyage
    # cloud client. "openai" routes everything through an OpenAI-compatible
    # `/v1/embeddings` endpoint — TEI, Infinity, vLLM, Together, Fireworks, etc.
    embed_provider: str = "voyage"
    embed_api_base_url: str | None = None      # e.g. http://tei-bge-m3:80/v1
    embed_api_key: str | None = None           # often "dummy" for self-hosted
    embed_model_name: str | None = None        # e.g. BAAI/bge-m3
    embed_dim: int | None = None               # vector dim served by that model

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"
    llm_rerank_model: str = "openai/gpt-4o-mini"

    # Optional per-role overrides — when set, that role uses its own endpoint
    # instead of the default openrouter_* pair. Useful for on-prem deployments
    # where, e.g., vision is served by a self-hosted vLLM Qwen3-VL while the
    # main LLM still goes through a cloud gateway.
    llm_api_base_url: str | None = None       # overrides openrouter_base_url for chat
    llm_api_key: str | None = None            # overrides openrouter_api_key for chat
    vision_api_base_url: str | None = None    # overrides for vision/OCR calls
    vision_api_key: str | None = None
    rerank_api_base_url: str | None = None    # overrides for rerank model
    rerank_api_key: str | None = None

    chunk_max_tokens: int = 500   # modern embedders (bge-m3, Qwen3-Embedding-8B) handle ≥8k context
    chunk_min_tokens: int = 200
    chunk_overlap: int = 40

    contextual_enrichment: bool = False
    contextual_enrichment_model: str = "openai/gpt-4o-mini"
    contextual_enrichment_batch: int = 8

    ingest_ocr_fallback: bool = True
    ingest_ocr_min_chars: int = 200
    ingest_ocr_render_dpi: int = 150
    llm_vision_model: str = "openai/gpt-4o-mini"

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 24
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    default_fts_language: str = "simple"

    retrieval_top_k_dense: int = 50
    retrieval_top_k_sparse: int = 50
    retrieval_top_k_hybrid: int = 30
    retrieval_rrf_k: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
