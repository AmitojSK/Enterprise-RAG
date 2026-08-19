"""Structured, privacy-conscious audit logging for security and operations."""

import logging
from uuid import UUID

audit_logger = logging.getLogger("enterprise_rag.audit")


def log_query(request_id: UUID, citation_count: int) -> None:
    """Record metadata, not the user's question or document content, to reduce PII exposure."""

    audit_logger.info("query_complete request_id=%s citations=%s", request_id, citation_count)


def log_ingestion(document_id: UUID, filename: str, chunk_count: int) -> None:
    """Record the operational outcome of ingestion without logging raw document text."""

    audit_logger.info("ingestion_complete document_id=%s filename=%s chunks=%s", document_id, filename, chunk_count)
