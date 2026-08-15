"""Adapter for an embedding provider, isolated so it is easy to replace later."""

from openai import OpenAI
from enterprise_rag.config import Settings


class OpenAIEmbeddingService:
    """Create dense vectors used for semantic retrieval."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for indexing and querying")
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch in one API call to reduce latency and cost."""

        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]
