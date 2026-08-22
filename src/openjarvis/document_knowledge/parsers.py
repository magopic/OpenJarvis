"""Document text extraction (FASE 4O.5).

V1 supported formats: PDF, TXT, Markdown. DOCX is deliberately NOT
included -- the audit for this phase found ``python-docx`` referenced in
``server/upload_router.py`` but never declared as a project dependency
(would install silently outside the declared dependency graph). Adding
real DOCX support means first adding a proper ``pyproject.toml`` extra
and validating it -- out of scope for "do not add broad format support
merely for completeness."

PDF extraction uses ``pdfplumber`` -- the established convention already
used by 3 independent modules in this codebase (``connectors/pipeline.py``,
``tools/pdf_tool.py``, ``tools/storage/ingest.py``), lazy-imported so a
missing/broken PDF dependency never breaks TXT/Markdown ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}


class UnsupportedDocumentError(Exception):
    """Raised for a file extension outside ``SUPPORTED_EXTENSIONS``."""


class DocumentParseError(Exception):
    """Raised when a supported file type fails to parse (malformed/corrupt)."""


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page's worth of text (PDF) or the whole file (TXT/MD, page=None)."""

    text: str
    page: Optional[int]  # 1-indexed; None when the format has no pages


def file_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext == ".txt":
        return "text"
    raise UnsupportedDocumentError(f"Unsupported file extension: {ext}")


def parse_document(path: Path) -> List[ParsedPage]:
    """Extract text, page-aware for PDF (STEP: "preserve page references
    whenever technically available"). TXT/Markdown return one page-less
    ``ParsedPage`` since those formats have no page concept."""
    ftype = file_type_for(path)  # raises UnsupportedDocumentError first, before any I/O

    if ftype == "pdf":
        return _parse_pdf(path)
    return [_parse_text(path)]


def _parse_text(path: Path) -> ParsedPage:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DocumentParseError(f"Could not read {path.name}: {exc}") from exc
    return ParsedPage(text=text, page=None)


def _parse_pdf(path: Path) -> List[ParsedPage]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise DocumentParseError(
            "PDF support requires the 'pdfplumber' package (already an optional "
            "extra in this project: `uv sync --extra pdf`)."
        ) from exc

    try:
        pages: List[ParsedPage] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append(ParsedPage(text=text, page=i))
        return pages
    except Exception as exc:  # pdfplumber raises assorted parser-internal errors on malformed PDFs
        raise DocumentParseError(f"Could not parse PDF {path.name}: {exc}") from exc


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "UnsupportedDocumentError",
    "DocumentParseError",
    "ParsedPage",
    "file_type_for",
    "parse_document",
]
