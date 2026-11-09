"""Unit tests for reranker dispatch and the Cohere cross-encoder path.

The Cohere path is a network call to Cohere's hosted cross-encoder; these tests
mock that single seam (``RAGService._cohere_rerank``) so nothing leaves the
process. ``RAGService`` is built with ``__new__`` to bypass ``__init__`` (which
would construct OpenAI/Qdrant clients) -- ``_rerank`` only needs ``.settings``.
"""

from unittest.mock import patch

from enterprise_rag.config import Settings
from enterprise_rag.services.rag import RAGService
from enterprise_rag.services.vector_store import RetrievedChunk


def _chunk(i: int) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=f"doc-{i}",
        filename="f.pdf",
        page_number=i,
        text=f"passage {i}",
        score=1.0 - i / 100,
    )


def _service(**overrides) -> RAGService:
    # `_env_file=None` ignores the developer's real .env so these tests are
    # deterministic regardless of local config (e.g. RERANKER=cohere set there).
    service = RAGService.__new__(RAGService)
    service.settings = Settings(_env_file=None, **overrides)
    return service


class TestRerankDispatch:
    def test_returns_as_is_when_at_or_below_rerank_k(self):
        # Fewer candidates than we keep: no provider call, no reordering.
        service = _service(reranker="cohere", cohere_api_key="key", rerank_k=8)
        chunks = [_chunk(i) for i in range(5)]
        with patch.object(RAGService, "_cohere_rerank") as seam:
            assert service._rerank("q", chunks) == chunks
        seam.assert_not_called()

    def test_missing_key_falls_back_to_llm(self):
        # "cohere" selected but no key -> use the LLM judge, not vector order.
        service = _service(reranker="cohere", cohere_api_key="", rerank_k=3)
        chunks = [_chunk(i) for i in range(6)]
        with patch.object(RAGService, "_rerank_llm", return_value=chunks[:3]) as llm:
            out = service._rerank("q", chunks)
        llm.assert_called_once()
        assert out == chunks[:3]

    def test_default_backend_is_llm(self):
        service = _service(rerank_k=3)  # reranker defaults to "llm"
        chunks = [_chunk(i) for i in range(6)]
        with patch.object(RAGService, "_rerank_llm", return_value=chunks[:3]) as llm:
            service._rerank("q", chunks)
        llm.assert_called_once()


class TestCohereRerank:
    def test_reorders_by_api_result(self):
        service = _service(reranker="cohere", cohere_api_key="key", rerank_k=3)
        chunks = [_chunk(i) for i in range(6)]
        # Cohere returns up to top_n results, sorted by relevance.
        fake = [
            {"index": 4, "relevance_score": 0.91},
            {"index": 0, "relevance_score": 0.80},
            {"index": 2, "relevance_score": 0.74},
        ]
        with patch.object(RAGService, "_cohere_rerank", return_value=fake) as seam:
            out = service._rerank("expense policy", chunks)
        seam.assert_called_once()
        assert [c.document_id for c in out] == ["doc-4", "doc-0", "doc-2"]
        assert len(out) == 3

    def test_failure_falls_back_to_vector_order(self):
        service = _service(reranker="cohere", cohere_api_key="key", rerank_k=3)
        chunks = [_chunk(i) for i in range(6)]
        with patch.object(RAGService, "_cohere_rerank", side_effect=RuntimeError("503")):
            out = service._rerank("q", chunks)
        # Vector order preserved (chunks arrive pre-sorted by Qdrant score).
        assert [c.document_id for c in out] == ["doc-0", "doc-1", "doc-2"]

    def test_ignores_out_of_range_indices(self):
        service = _service(reranker="cohere", cohere_api_key="key", rerank_k=3)
        chunks = [_chunk(i) for i in range(6)]
        fake = [{"index": 99, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]
        with patch.object(RAGService, "_cohere_rerank", return_value=fake):
            out = service._rerank("q", chunks)
        assert [c.document_id for c in out] == ["doc-1"]

    def test_empty_results_falls_back_to_vector_order(self):
        service = _service(reranker="cohere", cohere_api_key="key", rerank_k=3)
        chunks = [_chunk(i) for i in range(6)]
        with patch.object(RAGService, "_cohere_rerank", return_value=[]):
            out = service._rerank("q", chunks)
        assert [c.document_id for c in out] == ["doc-0", "doc-1", "doc-2"]

    def test_cohere_rerank_builds_request_and_parses_results(self):
        # Mock httpx at the seam's own call so we assert the request shape and the
        # results parsing without hitting the network.
        service = _service(reranker="cohere", cohere_api_key="secret", rerank_k=2, rerank_model="rerank-v3.5")

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"index": 1, "relevance_score": 0.9}]}

        with patch("enterprise_rag.services.rag.httpx.post", return_value=_Resp()) as post:
            results = service._cohere_rerank("my query", ["a", "b", "c"])

        assert results == [{"index": 1, "relevance_score": 0.9}]
        _, kwargs = post.call_args
        assert kwargs["json"] == {
            "model": "rerank-v3.5",
            "query": "my query",
            "documents": ["a", "b", "c"],
            "top_n": 2,
        }
        assert kwargs["headers"]["Authorization"] == "Bearer secret"
