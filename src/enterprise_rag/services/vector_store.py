"""Qdrant repository for the shared public document library."""

import logging
from dataclasses import dataclass
import time
from uuid import UUID, uuid5
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException
from enterprise_rag.config import Settings
from enterprise_rag.services.chunking import TextChunk


@dataclass
class RetrievedChunk:
    """A search result paired with the metadata needed to cite it."""

    document_id: str
    filename: str
    page_number: int | None
    text: str
    score: float


class VectorStoreUnavailable(RuntimeError):
    """Raised when a cloud vector write is still unavailable after safe retries."""


logger = logging.getLogger(__name__)


class QdrantStore:
    """Persistence boundary for document vectors and their metadata."""

    def __init__(self, settings: Settings) -> None:
        self.collection = settings.qdrant_collection
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout_seconds,
        )
        self.batch_size = settings.indexing_batch_size

    def ensure_collection(self, vector_size: int) -> None:
        """Create the collection and index required for optional document filtering.

        Qdrant Cloud requires a payload index before a field can be used as a
        filter. `document_id` makes the optional document filter efficient.
        Calling `create_payload_index` is safe for an existing collection and
        also repairs collections created before this index requirement existed.
        """

        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="document_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )

    def upsert(
        self,
        document_id: str,
        filename: str,
        chunks: list[TextChunk],
        vectors: list[list[float]],
    ) -> None:
        """Write text and metadata together with bounded, retryable cloud requests.

        Point IDs are deterministic from a document ID and chunk number. If a
        request times out after Qdrant has already accepted it, retrying updates
        the same points rather than creating duplicate searchable chunks.
        """

        self.ensure_collection(len(vectors[0]))
        document_uuid = UUID(document_id)
        points = [
            models.PointStruct(
                id=str(uuid5(document_uuid, f"chunk:{index}")), vector=vector,
                payload={
                    "document_id": document_id,
                    "filename": filename,
                    "chunk_index": index,
                    "page_number": chunk.page_number,
                    "text": chunk.text,
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors), start=1)
        ]
        for start in range(0, len(points), self.batch_size):
            self._upsert_batch(points[start : start + self.batch_size])

    def _upsert_batch(self, points: list[models.PointStruct]) -> None:
        """Retry only transient client timeouts; deterministic IDs make that safe."""

        for attempt in range(3):
            try:
                self.client.upsert(collection_name=self.collection, points=points, wait=True)
                return
            except ResponseHandlingException as error:
                # Do not hide deterministic validation errors behind retries.
                if "timed out" not in str(error).lower() or attempt == 2:
                    raise VectorStoreUnavailable(
                        "Qdrant did not confirm the document write. Please retry shortly."
                    ) from error
                # 1 then 2 seconds: enough for a transient cloud-side delay,
                # while keeping the request duration predictable for the UI.
                time.sleep(attempt + 1)

    def search(
        self,
        vector: list[float],
        limit: int,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Search the shared library; optional document IDs narrow the result set."""

        # This also ensures the payload indexes exist for collections created
        # before the first query, including collections already in Qdrant Cloud.
        self.ensure_collection(len(vector))
        info = self.client.get_collection(self.collection)
        logger.info("Collection '%s' has %s indexed points", self.collection, info.points_count)
        conditions: list[models.FieldCondition] = []
        if document_ids:
            conditions.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids)))
        results = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=models.Filter(must=conditions) if conditions else None,
            limit=limit,
            with_payload=True,
        ).points
        logger.info("query_points returned %d results (limit=%d, filter=%s)", len(results), limit, bool(document_ids))
        return [
            RetrievedChunk(
                document_id=item.payload["document_id"],
                filename=item.payload["filename"],
                page_number=item.payload.get("page_number"),
                text=item.payload["text"],
                score=item.score,
            )
            for item in results
        ]

    def delete_by_document(self, document_id: str) -> None:
        """Remove all vectors belonging to a document from the collection."""

        if not self.client.collection_exists(self.collection):
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                )
            ),
        )
