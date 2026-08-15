"""Unit tests for chunking, which has no network or database dependency."""

import pytest
from enterprise_rag.services.chunking import chunk_pages, chunk_text
from enterprise_rag.services.document_loader import SourcePage


def test_chunker_keeps_full_short_document() -> None:
    """A document smaller than the chunk limit should remain one intact chunk."""

    assert chunk_text("A short policy document.", chunk_size=100, overlap=10) == ["A short policy document."]


def test_chunker_rejects_invalid_overlap() -> None:
    """An overlap equal to the chunk size would prevent forward progress."""

    with pytest.raises(ValueError):
        chunk_text("text", chunk_size=10, overlap=10)


def test_pdf_page_number_stays_with_each_chunk() -> None:
    """Citations must preserve the actual page instead of exposing chunk IDs to users."""

    chunks = chunk_pages([SourcePage(text="A useful policy.", page_number=4)])
    assert len(chunks) == 1
    assert chunks[0].text == "A useful policy."
    assert chunks[0].page_number == 4
