"""Public HTTP endpoints for document ingestion and grounded questions."""

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from enterprise_rag.config import Settings, get_settings
from enterprise_rag.database import get_db
from enterprise_rag.models import DocumentRecord
from enterprise_rag.schemas import IngestResponse, QueryRequest, QueryResponse
from enterprise_rag.security import reject_prompt_injection
from enterprise_rag.services.audit import log_ingestion, log_query
from enterprise_rag.services.chunking import chunk_pages
from enterprise_rag.services.document_loader import SUPPORTED_SUFFIXES, extract_pages
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.rag import RAGService
from enterprise_rag.services.vector_store import QdrantStore, VectorStoreUnavailable

router = APIRouter(prefix="/v1", tags=["RAG"])


@router.post("/documents", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """Validate, parse, chunk, embed, and index one document in the public library.

    This endpoint intentionally has no login for a portfolio demo. Do not host
    it for confidential or user-specific documents without adding authentication
    and authorization first.
    """

    filename = file.filename or "unnamed"
    if not any(filename.lower().endswith(suffix) for suffix in SUPPORTED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"Supported types: {sorted(SUPPORTED_SUFFIXES)}")
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File is larger than MAX_UPLOAD_BYTES")
    # The fingerprint is based on bytes, not filename. Renaming the same PDF
    # will not waste embedding/Qdrant calls; changed content becomes new data.
    content_hash = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(DocumentRecord).where(
            DocumentRecord.content_hash == content_hash,
        )
    )
    if existing and existing.status == "indexed":
        return IngestResponse(
            document_id=existing.id,
            filename=existing.filename,
            chunks_indexed=existing.chunk_count,
            status="indexed",
            duplicate=True,
        )

    # A failed/interrupted attempt has the same fingerprint. Resume that record
    # rather than violating the content-hash uniqueness rule or making a second.
    record = existing or DocumentRecord(
        id=str(uuid4()),
        content_hash=content_hash,
        filename=filename,
    )
    record.status = "processing"
    record.error_message = None
    if existing is None:
        db.add(record)
    db.commit()
    try:
        chunks = chunk_pages(extract_pages(filename, content))
        if not chunks:
            raise ValueError("No readable text was found in this document")
        embeddings = OpenAIEmbeddingService(settings).embed([chunk.text for chunk in chunks])
        QdrantStore(settings).upsert(record.id, filename, chunks, embeddings)
    except ValueError as error:
        record.status = "failed"
        record.error_message = str(error)
        db.commit()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except VectorStoreUnavailable as error:
        # A temporary dependency outage should be clear to the user and should
        # not be reported as an application bug (HTTP 500).
        record.status = "failed"
        record.error_message = str(error)
        db.commit()
        raise HTTPException(status_code=503, detail=str(error)) from error
    record.status = "indexed"
    record.chunk_count = len(chunks)
    record.indexed_at = datetime.now(timezone.utc)
    db.commit()
    log_ingestion(UUID(record.id), filename, len(chunks))
    return IngestResponse(document_id=record.id, filename=filename, chunks_indexed=len(chunks))


@router.post("/query", response_model=QueryResponse)
def query_knowledge(
    request: QueryRequest,
    settings: Settings = Depends(get_settings),
) -> QueryResponse:
    """Answer a question with grounded retrieval and inspectable citations."""

    reject_prompt_injection(request.question)
    request_id = uuid4()
    answer, citations = RAGService(settings).answer(request.question, request.document_ids)
    log_query(request_id, len(citations))
    return QueryResponse(answer=answer, citations=citations, request_id=str(request_id))
