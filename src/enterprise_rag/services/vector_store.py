"""Qdrant repository for the shared public document library."""

import logging
from dataclasses import dataclass
from functools import lru_cache
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

# Collections and their payload indexes only need creating once per process, but
# ``QdrantStore`` is constructed per request, so this has to live at module scope
# rather than on the instance. Without it every single search spent three extra
# round trips on the vector store -- an existence check, a payload-index create
# with ``wait=True``, and a stats read -- before running the actual query.
#
# The trade-off: if a collection is deleted out from under a running process, it
# will not be recreated automatically, because this cache still says it exists.
# Nothing in this application deletes collections, so that only happens through
# operator action, and a restart clears it.
_ensured_collections: set[tuple[str, str, int]] = set()


@lru_cache(maxsize=4)
def get_qdrant_client(url: str, api_key: str | None, timeout: int) -> QdrantClient:
    """Return one client per cluster, shared for the life of the process.

    ``QdrantStore`` is built per request; building a client with it meant a new
    connection pool and a fresh TLS handshake on every query. Keyed on the
    connection parameters rather than ``Settings``, which is not hashable.
    """

    return QdrantClient(url=url, api_key=api_key, timeout=timeout)


class QdrantStore:
    """Persistence boundary for document vectors and their metadata."""

    def __init__(self, settings: Settings) -> None:
        self.collection = settings.qdrant_collection
        # Part of the cache key: the same collection name on a different cluster
        # is a different collection.
        self.url = settings.qdrant_url
        self.client = get_qdrant_client(
            settings.qdrant_url,
            settings.qdrant_api_key,
            settings.qdrant_timeout_seconds,
        )
        self.batch_size = settings.indexing_batch_size

    def ensure_collection(self, vector_size: int) -> None:
        """Create the collection and index required for optional document filtering.

        Qdrant Cloud requires a payload index before a field can be used as a
        filter. `document_id` makes the optional document filter efficient.
        Calling `create_payload_index` is safe for an existing collection and
        also repairs collections created before this index requirement existed.
        """

        cache_key = (self.url, self.collection, vector_size)
        if cache_key in _ensured_collections:
            return

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
        # Logged here rather than per query: it costs a round trip, and once per
        # process is enough to tell whether the collection holds anything.
        info = self.client.get_collection(self.collection)
        logger.info(
            "Collection '%s' ready with %s points (%s in an index)",
            self.collection, info.points_count, info.indexed_vectors_count,
        )
        # Two threads racing here both do idempotent work, so no lock is needed.
        _ensured_collections.add(cache_key)

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
        score_threshold: float = 0.0,
    ) -> list[RetrievedChunk]:
        """Search the shared library; optional document IDs narrow the result set."""

        # Ensures the payload index exists for collections created before the
        # first query, including ones already in Qdrant Cloud. Memoized per
        # process, so this is free after the first call.
        self.ensure_collection(len(vector))
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
        chunks = [
            RetrievedChunk(
                document_id=item.payload["document_id"],
                filename=item.payload["filename"],
                page_number=item.payload.get("page_number"),
                text=item.payload["text"],
                score=item.score,
            )
            for item in results
            if item.score >= score_threshold
        ]
        logger.info("%d chunks above score threshold %.2f", len(chunks), score_threshold)
        return chunks

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
