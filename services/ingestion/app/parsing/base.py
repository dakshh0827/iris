"""One entry point for text extraction: extract(path, file_type).

The rest of the pipeline never calls extract_pdf(), extract_html() or
extract_txt() directly. It calls extract() with the file type that intake
(Phase 2) found from the file's content, and a small "registry" (a dict from
file type to function) picks the right extractor.

Why a registry instead of if/elif? Adding a format later (say DOCX) means
writing one function and adding one line to EXTRACTORS; nothing else changes.

Try it from services/ingestion:   python -m app.parsing.base
(extracts all 10 files in data/regulations and prints a summary)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.errors import UnsupportedFileType
from app.intake.validation import FileType
from app.parsing.html import extract_html
from app.parsing.models import ExtractedDocument
from app.parsing.pdf import extract_pdf
from app.parsing.txt import extract_txt

logger = logging.getLogger(__name__)

# An extractor is any function that takes a file path and returns an ExtractedDocument.
Extractor = Callable[[Path], ExtractedDocument]

EXTRACTORS: dict[FileType, Extractor] = {
    "pdf": extract_pdf,
    "html": extract_html,
    "txt": extract_txt,
}


def extract(path: Path, file_type: FileType) -> ExtractedDocument:
    """Extract the text blocks of a stored file, using the extractor for its type.

    Raises:
      UnsupportedFileType  no extractor exists for file_type
      FileNotFoundError    the file isn't there (a storage problem, not a bad document)
      CorruptDocument      the PDF can't be opened (from extract_pdf)
    """
    extractor = EXTRACTORS.get(file_type)
    if extractor is None:
        raise UnsupportedFileType(
            f"There is no text extractor for file type '{file_type}'.",
            file_name=path.name,
            file_type=file_type,
        )
    # Checked here so every type fails the same way. (PyMuPDF has its own
    # FileNotFoundError that is not Python's, so callers couldn't catch it as one.)
    if not path.is_file():
        raise FileNotFoundError(f"No file at {path}")

    document = extractor(path)
    logger.info("%s: extracted %d blocks as %s, %d warnings",
                path.name, len(document.blocks), file_type, len(document.warnings))
    return document


def describe_location(document: ExtractedDocument, order: int) -> str:
    """A short, human-readable location for one block: 'page 2', '#p4', 'lines 3-4' or 'heading'."""
    block = document.blocks[order]
    if block.page is not None:
        return f"page {block.page}"
    if block.anchor is not None:
        return f"#{block.anchor}"
    if block.line_start is not None:
        return f"lines {block.line_start}-{block.line_end}"
    return "heading"  # an HTML heading without an id: its location is its own text


if __name__ == "__main__":
    from app.config import get_settings
    from app.intake.validation import SNIFF_SIZE, check_file_type
    from app.logging_config import configure_logging

    configure_logging()
    logging.getLogger().setLevel(logging.WARNING)  # show warnings only, not the info line per file

    print(f"{'file':<30} {'type':<5} {'pages':>5} {'blocks':>6} {'warn':>4}  {'first block':<12} text")
    for path in sorted(get_settings().regulations_dir.iterdir()):
        with path.open("rb") as file:
            file_type = check_file_type(path.name, file.read(SNIFF_SIZE))  # the same check as intake
        document = extract(path, file_type)
        pages = "-" if document.page_count is None else str(document.page_count)
        first = document.blocks[0]
        where = describe_location(document, 0)
        print(f"{path.name:<30} {file_type:<5} {pages:>5} {len(document.blocks):>6} "
              f"{len(document.warnings):>4}  {where:<12} {first.text.splitlines()[0][:40]}")