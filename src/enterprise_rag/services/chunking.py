"""Deterministic, overlap-aware text chunking used before embedding documents."""

import re
from dataclasses import dataclass
from enterprise_rag.services.document_loader import SourcePage


@dataclass(frozen=True)
class TextChunk:
    """A searchable text slice and the source page that produced it."""

    text: str
    page_number: int | None


def chunk_text(text: str, chunk_size: int = 1_000, overlap: int = 150) -> list[str]:
    """Split text near sentence boundaries while preserving a little preceding context."""

    if chunk_size <= overlap:
        raise ValueError("chunk_size must be larger than overlap")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(". ", start, end)
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunks.append(normalized[start:end].strip())
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_pages(pages: list[SourcePage]) -> list[TextChunk]:
    """Chunk pages independently so every PDF chunk has one unambiguous page citation."""

    return [
        TextChunk(text=chunk, page_number=page.page_number)
        for page in pages
        for chunk in chunk_text(page.text)
    ]
