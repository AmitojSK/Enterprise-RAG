"""Central, validated configuration loaded from environment variables."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Keeping secrets in the environment avoids hard-coding them."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = ""
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "enterprise_documents"
    max_upload_bytes: int = 10 * 1024 * 1024
    top_k: int = 20
    rerank_k: int = 8
    score_threshold: float = 0.25
    log_level: str = "INFO"
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-4o-mini"
    # SQLite keeps the project runnable locally. Render will provide a
    # postgresql+psycopg:// URL through its secret environment variables.
    database_url: str = "sqlite:///./data/enterprise_rag.db"
    # Cloud vector writes can exceed the client library's short default timeout.
    qdrant_timeout_seconds: int = 60
    indexing_batch_size: int = 32
    redis_url: str = "redis://localhost:6379/0"
    s3_bucket: str = ""
    s3_prefix: str = ""


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process so every request shares the same configuration."""

    return Settings()
