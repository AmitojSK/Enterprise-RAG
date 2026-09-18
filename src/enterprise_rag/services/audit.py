"""Structured, privacy-conscious audit logging for security and operations."""

import logging
from uuid import UUID

audit_logger = logging.getLogger("enterprise_rag.audit")


def log_query(request_id: UUID | str, citation_count: int, tokens: dict[str, int] | None = None) -> None:
    """Record metadata, not the user's question or document content, to reduce PII exposure.

    ``tokens`` is the per-request OpenAI usage (embedding + rerank + generation).
    Token spend is the metric most worth tracking for a RAG system -- it is the
    one that turns into a surprise bill -- and it is read off responses the code
    already holds, so logging it costs nothing extra.
    """

    if tokens is not None:
        audit_logger.info(
            "query_complete request_id=%s citations=%s prompt_tokens=%s completion_tokens=%s embedding_tokens=%s",
            request_id, citation_count, tokens["prompt"], tokens["completion"], tokens["embedding"],
        )
    else:
        audit_logger.info("query_complete request_id=%s citations=%s", request_id, citation_count)


def log_ingestion(document_id: UUID, filename: str, chunk_count: int) -> None:
    """Record the operational outcome of ingestion without logging raw document text."""

    audit_logger.info("ingestion_complete document_id=%s filename=%s chunks=%s", document_id, filename, chunk_count)
