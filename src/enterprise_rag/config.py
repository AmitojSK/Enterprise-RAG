"""Central, validated configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file rather than the process working directory. A bare
# ".env" is resolved relative to wherever the server happened to be started
# from, so launching uvicorn from another directory silently loaded no
# configuration at all and every setting fell back to its default -- pointing
# the app at a different Qdrant collection and a different SQLite file without
# reporting anything. Real environment variables still take precedence, so
# containers and hosting platforms are unaffected.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Runtime settings. Keeping secrets in the environment avoids hard-coding them."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")
    openai_api_key: str = ""
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "enterprise_documents"
    max_upload_bytes: int = 20 * 1024 * 1024
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
    # An empty value is an explicit opt-out from background ingestion: the API
    # then parses and embeds uploads inside the request instead of enqueuing.
    redis_url: str = "redis://localhost:6379/0"
    # Cloudflare R2 (S3-compatible) object storage for the original uploaded
    # files, so the frontend can render the source PDF beside each answer. All
    # four are required together; when any is blank, ingestion still indexes and
    # answers documents -- it just stores no viewable original. Never commit real
    # values: the key/secret belong in .env and render.env, both gitignored.
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    # Comma-separated browser origins allowed to call this API. A deployed
    # frontend is served from a different origin than the API, so its URL must
    # be listed here or the browser will block every request.
    allowed_origins: str = "http://localhost:4200,http://127.0.0.1:4200"


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process so every request shares the same configuration."""

    return Settings()
