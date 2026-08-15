"""Local parsers for the document formats supported by the ingestion endpoint."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from docx import Document
from pypdf import PdfReader


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


@dataclass(frozen=True)
class SourcePage:
    """Text from one page, or from a format that has no trustworthy page number."""

    text: str
    page_number: int | None


def extract_pages(filename: str, content: bytes) -> list[SourcePage]:
    """Extract text while retaining PDF page numbers for human-friendly citations.

    TXT, Markdown, and DOCX do not contain a dependable rendered page layout,
    so their page number is `None`. It is better to omit a page than invent one.
    """

    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return [SourcePage(text=content.decode("utf-8", errors="replace"), page_number=None)]
    if suffix == ".pdf":
        return [
            SourcePage(text=page.extract_text() or "", page_number=page_number)
            for page_number, page in enumerate(PdfReader(BytesIO(content)).pages, start=1)
        ]
    if suffix == ".docx":
        text = "\n".join(paragraph.text for paragraph in Document(BytesIO(content)).paragraphs)
        return [SourcePage(text=text, page_number=None)]
    raise ValueError(f"Unsupported file type: {suffix or 'no extension'}")
