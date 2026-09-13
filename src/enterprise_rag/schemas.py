"""Pydantic contracts make the HTTP API explicit and self-documenting."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """A question plus an optional list of document IDs to search."""

    question: str = Field(min_length=3, max_length=4_000)
    document_ids: list[str] | None = None


class Citation(BaseModel):
    """Human-readable evidence returned with each answer.

    ``document_id`` lets the frontend open the exact source file at the cited
    page without guessing from the (non-unique) filename.
    """

    document_id: str | None = None
    filename: str
    page_number: int | None = None
    excerpt: str


class QueryResponse(BaseModel):
    """A grounded answer, evidence, and a request ID for support/audit purposes."""

    answer: str
    citations: list[Citation]
    request_id: str


class IngestResponse(BaseModel):
    """Summary of one successful document ingestion operation."""

    document_id: str
    filename: str
    chunks_indexed: int
    status: str = "indexed"
    duplicate: bool = False


class DocumentListItem(BaseModel):
    """Summary row returned by the document listing endpoint."""

    document_id: str
    filename: str
    status: str
    chunk_count: int
    created_at: str
    indexed_at: str | None = None


class DocumentDetail(BaseModel):
    """Full metadata for a single document."""

    document_id: str
    filename: str
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: str
    indexed_at: str | None = None

