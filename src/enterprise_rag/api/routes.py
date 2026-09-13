"""Public HTTP endpoints for document ingestion and grounded questions."""

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from enterprise_rag.config import Settings, get_settings
from enterprise_rag.database import get_db
from enterprise_rag.models import DocumentRecord
from enterprise_rag.schemas import (
    DocumentDetail,
    DocumentListItem,
    IngestResponse,
    QueryRequest,
    QueryResponse,
)
from enterprise_rag.security import reject_prompt_injection
from enterprise_rag.services.audit import log_ingestion, log_query
from enterprise_rag.services.chunking import chunk_pages
from enterprise_rag.services.document_loader import SUPPORTED_SUFFIXES, extract_pages
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.object_store import (
    ObjectStore,
    ObjectStoreUnavailable,
    content_type_for,
    storage_key,
)
from enterprise_rag.services.rag import RAGService
from enterprise_rag.services.vector_store import QdrantStore, VectorStoreUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["RAG"])


def _celery_available(settings: Settings) -> bool:
    """Check whether the Celery broker (Redis) is reachable.

    An empty ``REDIS_URL`` is an explicit opt-out rather than a misconfiguration:
    deployments without a worker process (Render's free tier has none) ingest
    synchronously, and probing an address we know is absent only adds latency.
    """
    if not settings.redis_url:
        return False
    try:
        from enterprise_rag.services.tasks import celery_app
        conn = celery_app.connection()
        conn.connect()
        conn.close()
        return True
    except Exception:
        return False


@router.post("/documents", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """Validate, parse, chunk, embed, and index one document in the public library.

    Declared ``def`` rather than ``async def`` on purpose. Every dependency this
    endpoint touches is synchronous and blocking -- pypdf extraction, the OpenAI
    client, the Qdrant client, and SQLAlchemy -- and an ``async def`` endpoint
    runs on the event loop, so a single upload stalls every other request for as
    long as indexing takes. FastAPI runs ``def`` endpoints in a threadpool
    instead, which is what the rest of the routes in this module already rely on.

    This endpoint intentionally has no login for a portfolio demo. Do not host
    it for confidential or user-specific documents without adding authentication
    and authorization first.
    """

    filename = file.filename or "unnamed"
    if not any(filename.lower().endswith(suffix) for suffix in SUPPORTED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"Supported types: {sorted(SUPPORTED_SUFFIXES)}")
    # ``file.file`` is the underlying spooled temporary file: the synchronous
    # counterpart of ``await file.read()``, which is unavailable here.
    content = file.file.read()
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
        # Re-index: delete old vectors so the document gets fresh chunks.
        QdrantStore(settings).delete_by_document(existing.id)
        existing.status = "processing"
        existing.chunk_count = 0
        existing.error_message = None
        db.commit()

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

    # Dispatch to Celery when a broker is available; fall back to synchronous.
    if _celery_available(settings):
        from enterprise_rag.services.tasks import ingest_document_task
        ingest_document_task.delay(record.id, filename, base64.b64encode(content).decode("ascii"))
        return IngestResponse(
            document_id=record.id, filename=filename, chunks_indexed=0, status="processing",
        )

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
    # Keep the original bytes so the frontend can render the source document.
    # A storage failure must not fail an otherwise successful ingestion: the
    # document is already indexed and answerable, only its preview is missing.
    try:
        ObjectStore(settings).put(storage_key(record.id, filename), content, content_type_for(filename))
    except ObjectStoreUnavailable:
        logger.warning("Indexed %s but could not store its original for viewing", record.id, exc_info=True)
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


def _format_timestamp(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@router.get("/documents", response_model=list[DocumentListItem])
def list_documents(db: Session = Depends(get_db)) -> list[DocumentListItem]:
    """Return every document in the shared public library."""

    records = db.scalars(select(DocumentRecord).order_by(DocumentRecord.created_at.desc())).all()
    return [
        DocumentListItem(
            document_id=r.id,
            filename=r.filename,
            status=r.status,
            chunk_count=r.chunk_count,
            created_at=r.created_at.isoformat(),
            indexed_at=_format_timestamp(r.indexed_at),
        )
        for r in records
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentDetail:
    """Return full metadata for a single document."""

    record = db.get(DocumentRecord, document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetail(
        document_id=record.id,
        filename=record.filename,
        status=record.status,
        chunk_count=record.chunk_count,
        error_message=record.error_message,
        created_at=record.created_at.isoformat(),
        indexed_at=_format_timestamp(record.indexed_at),
    )


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> Response:
    """Serve the original uploaded bytes so the frontend can render the source.

    Proxying the bytes through the API (rather than handing out a presigned R2
    URL) keeps the bucket private and avoids configuring CORS on it: the browser
    only ever talks to this already-allowed origin. Files are small (<= 20 MB)
    and read on demand, so loading one into memory per request is acceptable.
    """

    record = db.get(DocumentRecord, document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    result = ObjectStore(settings).get(storage_key(document_id, record.filename))
    if result is None:
        # No stored original: storage is unconfigured, or the document was
        # indexed before file storage existed. Either way there is nothing to
        # preview, which the frontend surfaces as a re-upload prompt.
        raise HTTPException(status_code=404, detail="No stored file for this document")
    data, content_type = result
    return Response(
        content=data,
        media_type=content_type,
        # `inline` lets the browser/pdf.js render it in place rather than
        # forcing a download; the filename is used if the user saves it.
        headers={"Content-Disposition": f'inline; filename="{record.filename}"'},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> None:
    """Remove a document's metadata, vectors, and stored original file."""

    record = db.get(DocumentRecord, document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    QdrantStore(settings).delete_by_document(document_id)
    ObjectStore(settings).delete(storage_key(document_id, record.filename))
    db.delete(record)
    db.commit()


@router.post("/query/stream")
def query_stream(
    request: QueryRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream the answer token-by-token via SSE, with citations as a final event."""

    reject_prompt_injection(request.question)
    request_id = uuid4()
    token_iter, citations = RAGService(settings).stream_answer(request.question, request.document_ids)
    log_query(request_id, len(citations))

    def event_stream():
        for token in token_iter:
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'citations': [c.model_dump() for c in citations], 'request_id': str(request_id)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

