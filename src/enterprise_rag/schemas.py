"""Pydantic contracts make the HTTP API explicit and self-documenting."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """A user question plus optional metadata filters scoped to their tenant."""

    question: str = Field(min_length=3, max_length=4_000)
    document_ids: list[str] | None = None


class Citation(BaseModel):
    """Human-readable evidence returned with each answer."""

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
