"""Celery tasks for background document ingestion."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import Celery
from enterprise_rag.config import get_settings
from enterprise_rag.database import SessionLocal
from enterprise_rag.models import DocumentRecord
from enterprise_rag.services.audit import log_ingestion
from enterprise_rag.services.chunking import chunk_pages
from enterprise_rag.services.document_loader import extract_pages
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.vector_store import QdrantStore

logger = logging.getLogger(__name__)

settings = get_settings()
celery_app = Celery("enterprise_rag", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_track_started = True


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def ingest_document_task(self, document_id: str, filename: str, content_bytes: bytes) -> dict:
    """Parse, chunk, embed, and index a document outside the HTTP request cycle."""

    db = SessionLocal()
    try:
        record = db.get(DocumentRecord, document_id)
        if not record:
            return {"status": "error", "detail": "Record not found"}

        chunks = chunk_pages(extract_pages(filename, content_bytes))
        if not chunks:
            record.status = "failed"
            record.error_message = "No readable text was found in this document"
            db.commit()
            return {"status": "failed", "detail": record.error_message}

        embeddings = OpenAIEmbeddingService(settings).embed([c.text for c in chunks])
        QdrantStore(settings).upsert(document_id, filename, chunks, embeddings)

        record.status = "indexed"
        record.chunk_count = len(chunks)
        record.indexed_at = datetime.now(timezone.utc)
        db.commit()
        log_ingestion(UUID(document_id), filename, len(chunks))
        return {"status": "indexed", "chunks": len(chunks)}
    except Exception as exc:
        db.rollback()
        record = db.get(DocumentRecord, document_id)
        if record:
            record.status = "failed"
            record.error_message = str(exc)[:500]
            db.commit()
        logger.exception("Background ingestion failed for %s", document_id)
        raise self.retry(exc=exc)
    finally:
        db.close()
