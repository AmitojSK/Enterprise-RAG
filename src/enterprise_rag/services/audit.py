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

    # The `extra` fields become top-level keys under JSON logging (LOG_FORMAT=json)
    # so an aggregator can filter on them; the message stays greppable under text.
    if tokens is not None:
        audit_logger.info(
            "query_complete request_id=%s citations=%s prompt_tokens=%s completion_tokens=%s embedding_tokens=%s",
            request_id, citation_count, tokens["prompt"], tokens["completion"], tokens["embedding"],
            extra={
                "event": "query_complete", "citations": citation_count,
                "prompt_tokens": tokens["prompt"], "completion_tokens": tokens["completion"],
                "embedding_tokens": tokens["embedding"],
            },
        )
    else:
        audit_logger.info(
            "query_complete request_id=%s citations=%s", request_id, citation_count,
            extra={"event": "query_complete", "citations": citation_count},
        )


def log_ingestion(document_id: UUID, filename: str, chunk_count: int) -> None:
    """Record the operational outcome of ingestion without logging raw document text."""

    # `doc_filename`, not `filename`: the latter is a reserved LogRecord attribute
    # and passing it via `extra` would raise.
    audit_logger.info(
        "ingestion_complete document_id=%s filename=%s chunks=%s", document_id, filename, chunk_count,
        extra={
            "event": "ingestion_complete", "document_id": str(document_id),
            "doc_filename": filename, "chunks": chunk_count,
        },
    )
