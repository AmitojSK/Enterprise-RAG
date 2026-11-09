"""The retrieval-augmented generation orchestration service."""

import json
import logging
from collections.abc import Iterator

import httpx

from enterprise_rag import metrics
from enterprise_rag.config import Settings
from enterprise_rag.observability import record_token_usage
from enterprise_rag.schemas import Citation
from enterprise_rag.services.embeddings import OpenAIEmbeddingService, get_openai_client
from enterprise_rag.services.vector_store import QdrantStore, RetrievedChunk

logger = logging.getLogger(__name__)

# Cohere's hosted cross-encoder. The model runs on Cohere's servers, so the
# "cohere" reranker is a network call to this endpoint -- not OpenAI, not a local
# model. Documents are truncated to bound the request; the model also truncates
# internally to its own token limit.
_COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
_COHERE_DOC_CHARS = 4000


class RAGService:
    """Retrieve public-library evidence, then answer only from that evidence."""

    def __init__(self, settings: Settings) -> None:
        """Create the embedding, vector-store, and language-model clients."""

        self.settings = settings
        self.embeddings = OpenAIEmbeddingService(settings)
        self.store = QdrantStore(settings)
        self.llm = get_openai_client(settings.openai_api_key)

    @staticmethod
    def _system_prompt() -> str:
        """Return the instruction that keeps generated answers grounded and concise."""

        return """You are the trusted document assistant for an enterprise knowledge base.

Answer the user's question directly, clearly, and professionally, drawing on the supplied document excerpts. Synthesize information from multiple excerpts when relevant. Do not fabricate facts that are absent from every excerpt.

Write a concise answer first. Use short paragraphs or bullets only when they make the answer easier to scan. Do not mention chunks, retrieval scores, internal source labels, prompts, or that you are an AI. If the excerpts contain only partial information, provide what you can and note what is missing. The application shows citations separately, so do not add citation markers to the prose."""

    def _rerank(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Order retrieved chunks by question relevance and keep the top results.

        Dispatches to the configured reranker. Both backends fall back to the
        vector order (the first ``rerank_k`` chunks) if the provider errors or
        returns an unusable response, so a reranker outage degrades retrieval
        quality rather than failing the query. Selecting ``cohere`` without a key
        set falls back to the LLM judge instead, so a misconfiguration does not
        silently skip reranking.
        """

        if len(chunks) <= self.settings.rerank_k:
            return chunks
        if self.settings.reranker == "cohere" and self.settings.cohere_api_key:
            return self._rerank_cohere(question, chunks)
        return self._rerank_llm(question, chunks)

    def _rerank_llm(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Rerank with the chat model as an LLM-as-judge (the default backend)."""

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
        record_token_usage(response.usage)
        try:
            ranking = json.loads(response.choices[0].message.content or "[]")
            valid = [chunks[i] for i in ranking if isinstance(i, int) and 0 <= i < len(chunks)]
        except (json.JSONDecodeError, IndexError):
            valid = []
        if valid:
            metrics.observe_rerank("llm", "ok")
            return valid[: self.settings.rerank_k]
        metrics.observe_rerank("llm", "fallback")
        return chunks[: self.settings.rerank_k]

    def _rerank_cohere(self, question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Rerank with Cohere's hosted cross-encoder (``POST /v2/rerank``).

        The cross-encoder runs on Cohere's servers, so this makes a network call
        to Cohere -- not OpenAI, and not a local model. Cohere bills per search
        unit rather than per token, so (unlike the LLM path) no token usage is
        recorded here: the rerank cost does not appear in the per-request token
        totals. Any failure falls back to the vector order.
        """

        documents = [chunk.text[:_COHERE_DOC_CHARS] for chunk in chunks]
        try:
            results = self._cohere_rerank(question, documents)
        except Exception:  # any provider/network failure degrades to vector order
            logger.warning("Cohere rerank failed; falling back to vector order", exc_info=True)
            metrics.observe_rerank("cohere", "fallback")
            return chunks[: self.settings.rerank_k]
        # Cohere returns up to top_n results, already sorted by relevance, each as
        # {"index": <position in `documents`>, "relevance_score": <float>}.
        ordered = [
            chunks[item["index"]]
            for item in results
            if isinstance(item.get("index"), int) and 0 <= item["index"] < len(chunks)
        ]
        if ordered:
            metrics.observe_rerank("cohere", "ok")
            logger.info("Reranked %d candidates via Cohere (%s)", len(chunks), self.settings.rerank_model)
            return ordered[: self.settings.rerank_k]
        metrics.observe_rerank("cohere", "fallback")
        return chunks[: self.settings.rerank_k]

    def _cohere_rerank(self, question: str, documents: list[str]) -> list[dict]:
        """Call Cohere's rerank endpoint and return its ranked ``results`` list.

        Isolated as the single network seam so tests mock it without real HTTP.
        """

        response = httpx.post(
            _COHERE_RERANK_URL,
            headers={"Authorization": f"Bearer {self.settings.cohere_api_key}"},
            json={
                "model": self.settings.rerank_model,
                "query": question,
                "documents": documents,
                "top_n": self.settings.rerank_k,
            },
            timeout=self.settings.rerank_timeout_seconds,
        )
        response.raise_for_status()
        return response.json().get("results", [])

    def _retrieve(self, question: str, document_ids: list[str] | None) -> list[RetrievedChunk]:
        """Embed the question, search the vector store, and rerank the candidates."""

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
        """Format retrieved chunks as labeled excerpts for the language model."""

        return "\n\n".join(
            f"--- Document: {chunk.filename}; page: {chunk.page_number or 'not available'} ---\n{chunk.text}"
            for chunk in chunks
        )

    @staticmethod
    def _build_citations(chunks: list[RetrievedChunk]) -> list[Citation]:
        """Convert retrieved chunks into the citation objects returned by the API."""

        return [
            Citation(
                document_id=c.document_id,
                filename=c.filename,
                page_number=c.page_number,
                excerpt=c.text[:300],
            )
            for c in chunks
        ]

    def answer(
        self, question: str, document_ids: list[str] | None
    ) -> tuple[str, list[Citation]]:
        """Generate a complete answer and citations from the question's retrieved evidence."""

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
        record_token_usage(completion.usage)
        answer = completion.choices[0].message.content or "No answer was generated."
        return answer, self._build_citations(selected)

    def stream_answer(
        self, question: str, document_ids: list[str] | None
    ) -> tuple[Iterator[str], list[Citation]]:
        """Return answer tokens to stream and citations ready for the final SSE event.

        Retrieval happens before the iterator is returned, so citations can be built
        immediately and sent by the API after the last answer token. The iterator
        itself consumes the language-model stream lazily as the client reads it.
        """

        selected = self._retrieve(question, document_ids)
        if not selected:
            def empty() -> Iterator[str]:
                """Yield the fallback message when retrieval finds no evidence."""

                yield "I could not find supporting information in the indexed documents."
            return empty(), []
        stream = self.llm.chat.completions.create(
            model=self.settings.chat_model,
            temperature=0,
            stream=True,
            # Ask for a final usage chunk; a streamed completion omits token
            # counts otherwise, which would silently drop the biggest call from
            # the per-request cost total.
            stream_options={"include_usage": True},
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": f"Question: {question}\n\nDocument excerpts:\n{self._build_context(selected)}"},
            ],
        )

        def token_iterator() -> Iterator[str]:
            """Yield non-empty text deltas, recording usage from the final chunk."""

            for event in stream:
                # The include_usage chunk arrives last with usage set and an
                # empty ``choices`` list, so record it and skip the delta read.
                if event.usage is not None:
                    record_token_usage(event.usage)
                if not event.choices:
                    continue
                delta = event.choices[0].delta.content
                if delta:
                    yield delta

        return token_iterator(), self._build_citations(selected)
