"""S3 connector that scans a bucket prefix and indexes new documents."""

import hashlib
import logging
from uuid import uuid4

import boto3
from enterprise_rag.config import Settings
from enterprise_rag.database import SessionLocal
from enterprise_rag.models import DocumentRecord
from enterprise_rag.services.document_loader import SUPPORTED_SUFFIXES

logger = logging.getLogger(__name__)


def scan_and_enqueue(settings: Settings) -> list[dict]:
    """List objects under the configured S3 prefix and enqueue unseen documents."""

    if not settings.s3_bucket:
        return []

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    results: list[dict] = []
    db = SessionLocal()

    try:
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=settings.s3_prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if not any(key.lower().endswith(s) for s in SUPPORTED_SUFFIXES):
                    continue

                body = s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
                content_hash = hashlib.sha256(body).hexdigest()

                existing = db.query(DocumentRecord).filter_by(content_hash=content_hash).first()
                if existing:
                    results.append({"key": key, "status": "duplicate"})
                    continue

                filename = key.rsplit("/", 1)[-1]
                record = DocumentRecord(
                    id=str(uuid4()), content_hash=content_hash,
                    filename=filename, status="processing",
                )
                db.add(record)
                db.commit()

                if _dispatch_task(record.id, filename, body):
                    results.append({"key": key, "status": "queued", "document_id": record.id})
                else:
                    results.append({"key": key, "status": "queued_sync", "document_id": record.id})
    finally:
        db.close()

    return results


def _dispatch_task(document_id: str, filename: str, content: bytes) -> bool:
    """Try Celery; return False if broker unavailable so caller knows it ran inline."""
    try:
        from enterprise_rag.services.tasks import celery_app, ingest_document_task
        conn = celery_app.connection()
        conn.connect()
        conn.close()
        ingest_document_task.delay(document_id, filename, content)
        return True
    except Exception:
        # Synchronous fallback
        from enterprise_rag.services.tasks import ingest_document_task
        ingest_document_task(document_id, filename, content)
        return False
