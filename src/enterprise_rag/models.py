"""Relational records that make document ingestion observable and idempotent."""

from datetime import datetime
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from enterprise_rag.database import Base


class DocumentRecord(Base):
    """One content-addressed document in the shared public demo library.

    `content_hash` is a SHA-256 fingerprint of the original file bytes. The
    unique constraint prevents an identical document from being indexed twice
    more than once, including after a browser refresh.
    """

    # A new table avoids silently changing the schema of an earlier
    # authentication-enabled version of this learning project.
    __tablename__ = "public_documents"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_public_document_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="processing")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
