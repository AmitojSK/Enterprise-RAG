"""Integration tests for document CRUD and streaming endpoints."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from enterprise_rag.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_db():
    """Roll back every test's database writes so tests stay independent."""
    from enterprise_rag.database import SessionLocal
    db = SessionLocal()
    db.begin_nested()
    yield db
    db.rollback()
    db.close()


def _seed_document(db, *, status: str = "indexed", chunk_count: int = 3) -> str:
    from enterprise_rag.models import DocumentRecord
    doc_id = str(uuid4())
    db.add(DocumentRecord(
        id=doc_id, content_hash=f"hash-{doc_id}", filename="test.pdf",
        status=status, chunk_count=chunk_count,
    ))
    db.commit()
    return doc_id


class TestListDocuments:
    def test_empty_library_returns_empty_list(self):
        response = client.get("/v1/documents")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestGetDocument:
    def test_missing_document_returns_404(self):
        response = client.get(f"/v1/documents/{uuid4()}")
        assert response.status_code == 404


class TestDeleteDocument:
    def test_missing_document_returns_404(self):
        response = client.delete(f"/v1/documents/{uuid4()}")
        assert response.status_code == 404


class TestQueryStream:
    @patch("enterprise_rag.api.routes.RAGService")
    def test_stream_returns_sse_events(self, mock_rag_cls):
        from enterprise_rag.schemas import Citation
        mock_service = MagicMock()
        mock_service.stream_answer.return_value = (
            iter(["Hello", " world"]),
            [Citation(filename="doc.pdf", page_number=1, excerpt="test excerpt")],
        )
        mock_rag_cls.return_value = mock_service

        response = client.post("/v1/query/stream", json={"question": "What is the policy?"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert '"token": "Hello"' in body or '"token":"Hello"' in body
        assert "[DONE]" in body
