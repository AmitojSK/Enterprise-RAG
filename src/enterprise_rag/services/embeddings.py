"""Adapter for an embedding provider, isolated so it is easy to replace later."""

from functools import lru_cache
from openai import OpenAI
from enterprise_rag.config import Settings
from enterprise_rag.observability import record_token_usage


@lru_cache(maxsize=4)
def get_openai_client(api_key: str) -> OpenAI:
    """Return one client per API key, shared for the life of the process.

    These services are constructed per request, and building a client each time
    means a fresh connection pool -- so every call paid for DNS, a TCP connect
    and a TLS handshake before sending anything. The underlying ``httpx`` client
    is thread-safe, which matters because the routes run in FastAPI's threadpool.
    Keyed on the API key rather than ``Settings`` because pydantic models are not
    hashable.
    """

    return OpenAI(api_key=api_key)


class OpenAIEmbeddingService:
    """Create dense vectors used for semantic retrieval."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for indexing and querying")
        self.client = get_openai_client(settings.openai_api_key)
        self.model = settings.embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch in one API call to reduce latency and cost."""

        response = self.client.embeddings.create(model=self.model, input=texts)
        record_token_usage(response.usage, embedding=True)
        return [item.embedding for item in response.data]
