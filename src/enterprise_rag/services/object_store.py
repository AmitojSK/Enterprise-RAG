"""Cloudflare R2 (S3-compatible) storage for the original uploaded files.

Vectors and chunk text live in Qdrant; this keeps the *source* bytes so the
frontend can render the actual PDF beside a cited answer. Storage is optional:
when R2 is not configured, ingestion still indexes and answers documents, and
the file endpoint simply reports that no preview is available.
"""

import logging
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from enterprise_rag.config import Settings

logger = logging.getLogger(__name__)

# Content types we set on upload and fall back to on download, keyed by suffix.
# PDF is the one the viewer renders; the rest are stored for completeness and
# download, but have no page-accurate preview.
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def content_type_for(filename: str) -> str:
    """Best-effort MIME type from a filename suffix."""

    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def storage_key(document_id: str, filename: str) -> str:
    """Derive the object key from the document ID, so no extra column is needed.

    The key is deterministic, so re-indexing the same document overwrites its
    object rather than orphaning one, and the file endpoint can locate bytes
    from the document record alone. Documents indexed before storage existed
    simply have no object at their key, which the caller treats as "no preview".
    """

    return f"documents/{document_id}{Path(filename).suffix.lower()}"


class ObjectStoreUnavailable(RuntimeError):
    """Raised when a configured object store fails a read or write request."""


@lru_cache(maxsize=2)
def _get_client(endpoint: str, access_key: str, secret_key: str):
    """Return one boto3 S3 client per credential set, reused across requests.

    Like the Qdrant client, ``ObjectStore`` is constructed per request; without
    this cache every upload or fetch would build a fresh connection pool and TLS
    handshake. R2 speaks the S3 API, so the standard client works against its
    endpoint once ``region_name`` is set to the ``auto`` value R2 expects.
    """

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


class ObjectStore:
    """Persistence boundary for the original bytes of uploaded documents."""

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.r2_bucket
        self._configured = all(
            (settings.r2_endpoint, settings.r2_bucket, settings.r2_access_key_id, settings.r2_secret_access_key)
        )
        self._client = (
            _get_client(settings.r2_endpoint, settings.r2_access_key_id, settings.r2_secret_access_key)
            if self._configured
            else None
        )

    @property
    def configured(self) -> bool:
        """Whether object storage is set up; callers degrade gracefully when not."""

        return self._configured

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store bytes under ``key``. A no-op when storage is not configured."""

        if not self._client:
            return
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (ClientError, BotoCoreError) as error:
            raise ObjectStoreUnavailable("Could not store the original file.") from error

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Return ``(bytes, content_type)``, or ``None`` if the object is absent.

        A missing object is expected -- it is how a pre-storage document, or one
        whose upload failed, reports "no preview" -- so it is returned as ``None``
        rather than raised. Only genuine transport/permission failures raise.
        """

        if not self._client:
            return None
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "NoSuchBucket", "404"}:
                return None
            raise ObjectStoreUnavailable("Could not read the stored file.") from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailable("Could not read the stored file.") from error
        return response["Body"].read(), response.get("ContentType", "application/octet-stream")

    def delete(self, key: str) -> None:
        """Best-effort delete: a missing object already satisfies the intent."""

        if not self._client:
            return
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError):
            logger.warning("Could not delete object %s from R2", key, exc_info=True)
