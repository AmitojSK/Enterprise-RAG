"""Authenticated HTTP endpoints for document ingestion and grounded questions."""

from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from enterprise_rag.config import Settings, get_settings
from enterprise_rag.schemas import IngestResponse, QueryRequest, QueryResponse
from enterprise_rag.security import Principal, current_principal, reject_prompt_injection, require_admin
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
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    """Validate, parse, chunk, embed, and index one document for the admin's tenant."""

    filename = file.filename or "unnamed"
    if not any(filename.lower().endswith(suffix) for suffix in SUPPORTED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"Supported types: {sorted(SUPPORTED_SUFFIXES)}")
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File is larger than MAX_UPLOAD_BYTES")
    try:
        chunks = chunk_pages(extract_pages(filename, content))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not chunks:
        raise HTTPException(status_code=422, detail="No readable text was found in this document")
    document_id = uuid4()
    embeddings = OpenAIEmbeddingService(settings).embed([chunk.text for chunk in chunks])
    try:
        QdrantStore(settings).upsert(
            principal.tenant_id, str(document_id), filename, chunks, embeddings
        )
    except VectorStoreUnavailable as error:
        # A temporary dependency outage should be clear to the user and should
        # not be reported as an application bug (HTTP 500).
        raise HTTPException(status_code=503, detail=str(error)) from error
    log_ingestion(principal.tenant_id, document_id, filename, len(chunks))
    return IngestResponse(document_id=str(document_id), filename=filename, chunks_indexed=len(chunks))


@router.post("/query", response_model=QueryResponse)
def query_knowledge(
    request: QueryRequest,
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
) -> QueryResponse:
    """Answer a question with tenant-scoped retrieval and independently inspectable citations."""

    reject_prompt_injection(request.question)
    request_id = uuid4()
    answer, citations = RAGService(settings).answer(
        principal.tenant_id, request.question, request.document_ids
    )
    log_query(request_id, principal.tenant_id, len(citations))
    return QueryResponse(answer=answer, citations=citations, request_id=str(request_id))
