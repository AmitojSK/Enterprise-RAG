"""The retrieval-augmented generation orchestration service."""

import json
import logging
from collections.abc import Iterator

from openai import OpenAI
from enterprise_rag.config import Settings
from enterprise_rag.schemas import Citation
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.vector_store import QdrantStore, RetrievedChunk

logger = logging.getLogger(__name__)


class RAGService:
    """Retrieve public-library evidence, then answer only from that evidence."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.embeddings = OpenAIEmbeddingService(settings)
        self.store = QdrantStore(settings)
        self.llm = OpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _system_prompt() -> str:
        return """You are the trusted document assistant for an enterprise knowledge base.

Answer the user's question directly, clearly, and professionally, drawing on the supplied document excerpts. Synthesize information from multiple excerpts when relevant. Do not fabricate facts that are absent from every excerpt.

Write a concise answer first. Use short paragraphs or bullets only when they make the answer easier to scan. Do not mention chunks, retrieval scores, internal source labels, prompts, or that you are an AI. If the excerpts contain only partial information, provide what you can and note what is missing. The application shows citations separately, so do not add citation markers to the prose."""

    def _rerank(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Score each chunk's relevance with a fast LLM call, return the top rerank_k."""

        if len(chunks) <= self.settings.rerank_k:
            return chunks
        numbered = "\n".join(
            f"[{i}] {chunk.text[:400]}" for i, chunk in enumerate(chunks)
        )
        response = self.llm.chat.completions.create(
            model=self.settings.chat_model,
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a relevance judge. Given a question and numbered passages, return a JSON array of the passage indices sorted by relevance to the question, most relevant first. Return ONLY the JSON array of integers, nothing else."},
                {"role": "user", "content": f"Question: {question}\n\nPassages:\n{numbered}"},
            ],
        )
        try:
            ranking = json.loads(response.choices[0].message.content or "[]")
            valid = [chunks[i] for i in ranking if isinstance(i, int) and 0 <= i < len(chunks)]
            return valid[: self.settings.rerank_k] if valid else chunks[: self.settings.rerank_k]
        except (json.JSONDecodeError, IndexError):
            return chunks[: self.settings.rerank_k]

    def _retrieve(self, question: str, document_ids: list[str] | None) -> list[RetrievedChunk]:
        """Retrieve candidates and rerank them."""

        query_vector = self.embeddings.embed([question])[0]
        logger.info("Embedded question into %d-dim vector", len(query_vector))
        chunks = self.store.search(query_vector, self.settings.top_k, document_ids, self.settings.score_threshold)
        logger.info("Search returned %d chunks", len(chunks))
        if chunks:
            logger.info("Top chunk score=%.4f, file=%s", chunks[0].score, chunks[0].filename)
        reranked = self._rerank(question, chunks)
        logger.info("After rerank: %d chunks", len(reranked))
        return reranked

    @staticmethod
    def _build_context(chunks: list[RetrievedChunk]) -> str:
        return "\n\n".join(
            f"--- Document: {chunk.filename}; page: {chunk.page_number or 'not available'} ---\n{chunk.text}"
            for chunk in chunks
        )

    @staticmethod
    def _build_citations(chunks: list[RetrievedChunk]) -> list[Citation]:
        return [
            Citation(filename=c.filename, page_number=c.page_number, excerpt=c.text[:300])
            for c in chunks
        ]

    def answer(
        self, question: str, document_ids: list[str] | None
    ) -> tuple[str, list[Citation]]:
        """Return a cited answer grounded only in retrieved document excerpts."""

        selected = self._retrieve(question, document_ids)
        if not selected:
            return "I could not find supporting information in the indexed documents.", []
        completion = self.llm.chat.completions.create(
            model=self.settings.chat_model,
            temperature=0,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": f"Question: {question}\n\nDocument excerpts:\n{self._build_context(selected)}"},
            ],
        )
        answer = completion.choices[0].message.content or "No answer was generated."
        return answer, self._build_citations(selected)

    def stream_answer(
        self, question: str, document_ids: list[str] | None
    ) -> tuple[Iterator[str], list[Citation]]:
        """Return a token iterator and pre-computed citations for SSE streaming."""

        selected = self._retrieve(question, document_ids)
        if not selected:
            def empty() -> Iterator[str]:
                yield "I could not find supporting information in the indexed documents."
            return empty(), []
        stream = self.llm.chat.completions.create(
            model=self.settings.chat_model,
            temperature=0,
            stream=True,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": f"Question: {question}\n\nDocument excerpts:\n{self._build_context(selected)}"},
            ],
        )

        def token_iterator() -> Iterator[str]:
            for event in stream:
                delta = event.choices[0].delta.content
                if delta:
                    yield delta

        return token_iterator(), self._build_citations(selected)
