"""Celery tasks for background document ingestion."""

import base64
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import Celery
from enterprise_rag.config import get_settings
from enterprise_rag.database import SessionLocal, engine
from enterprise_rag.models import DocumentRecord
from enterprise_rag.services.audit import log_ingestion
from enterprise_rag.services.chunking import chunk_pages
from enterprise_rag.services.document_loader import extract_pages
from enterprise_rag.services.embeddings import OpenAIEmbeddingService
from enterprise_rag.services.object_store import (
    ObjectStore,
    ObjectStoreUnavailable,
    content_type_for,
    storage_key,
)
from enterprise_rag.services.vector_store import QdrantStore

logger = logging.getLogger(__name__)

settings = get_settings()
celery_app = Celery("enterprise_rag", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_track_started = True

# The worker process must create the schema that the API also uses.
from enterprise_rag.database import initialize_database  # noqa: E402
initialize_database()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def ingest_document_task(self, document_id: str, filename: str, content_b64: str) -> dict:
    """Parse, chunk, embed, and index a document outside the HTTP request cycle."""

    content_bytes = base64.b64decode(content_b64)
    db = SessionLocal()
    try:
        record = db.get(DocumentRecord, document_id)
        if not record:
            # The API commits this row before queueing the task, so a normal
            # first ingestion always finds it. Reaching here means the row was
            # deleted while the task waited, or -- far more often -- that this
            # worker is pointed at a different database than the API. Returning
            # quietly would mark the task successful and leave the document
            # stuck on "processing" forever, so say so loudly.
            logger.error(
                "No record for document_id=%s; this worker is using %s. The document "
                "will stay in 'processing' because no status can be written.",
                document_id,
                engine.url.render_as_string(hide_password=True),
            )
            return {"status": "error", "detail": "Record not found"}

        chunks = chunk_pages(extract_pages(filename, content_bytes))
        if not chunks:
            record.status = "failed"
            record.error_message = "No readable text was found in this document"
            db.commit()
            return {"status": "failed", "detail": record.error_message}

        embeddings = OpenAIEmbeddingService(settings).embed([c.text for c in chunks])
        QdrantStore(settings).upsert(document_id, filename, chunks, embeddings)
        # Store the original bytes for the source viewer. A storage failure here
        # must not fail ingestion -- the document is indexed and answerable -- so
        # it is logged rather than raised.
        try:
            ObjectStore(settings).put(
                storage_key(document_id, filename), content_bytes, content_type_for(filename)
            )
        except ObjectStoreUnavailable:
            logger.warning("Indexed %s but could not store its original for viewing", document_id, exc_info=True)

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
