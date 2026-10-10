"""PDF text extraction with PyMuPDF, page by page.

Each PDF page is read separately, and every text block keeps its page
number. We never join the pages first: once joined, nobody can tell where
page 1 ended, and citations like "page 2" become impossible.

PyMuPDF splits a page into "blocks", usually one per paragraph. Each block
comes as a tuple: (x0, y0, x1, y1, text, block_number, block_type), where
block_type 0 is text and 1 is an image.

A file PyMuPDF can't open, or one that needs a password, raises
CorruptDocument (Step 5). A damaged file PyMuPDF manages to repair is read,
with a warning, because some of its text may be missing.

A page without text is not an error, but it is worth a warning (Step 4):
  - a page with nothing on it, like EMIR_2021_v1.pdf page 2, is just blank;
  - a page with images but no text is probably scanned: its words are only
    pixels, so we can't read them without OCR (optical character recognition).

Try it from services/ingestion:
    python -m app.parsing.pdf                       (MiFID_II_2023_v1.pdf)
    python -m app.parsing.pdf EMIR_2021_v2.pdf      (any PDF in data/regulations)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from app.errors import CorruptDocument
from app.parsing.models import ExtractedDocument, TextBlock

logger = logging.getLogger(__name__)

TEXT_BLOCK = 0  # block_type value for text (1 means image)

# MuPDF (the C library inside PyMuPDF) prints its own messages straight to the
# screen, e.g. "MuPDF error: format error: ..." for a damaged file. We report
# problems ourselves (CorruptDocument, warnings), so we switch those off.
pymupdf.TOOLS.mupdf_display_errors(False)


def extract_pdf(path: Path) -> ExtractedDocument:
    """Read every page of a PDF and return its text blocks with page numbers.

    Blocks keep PyMuPDF's order within a page, and pages are read in order,
    so `order` follows the document from start to end. Only the whitespace
    around each block is removed; everything else is kept exactly as
    extracted (Phase 4 does the cleaning). Pages without text add a warning.
    """
    blocks: list[TextBlock] = []
    warnings: list[str] = []
    scanned_pages: list[int] = []
    try:
        pdf = pymupdf.open(path)
    except pymupdf.FileDataError as error:  # also covers pymupdf.EmptyFileError
        raise CorruptDocument(
            "The PDF can't be opened: it is damaged or not a real PDF.",
            file_name=path.name,
        ) from error

    with pdf:
        if pdf.needs_pass:
            raise CorruptDocument(
                "The PDF is password-protected. Upload a copy without a password.",
                file_name=path.name,
            )
        if pdf.is_repaired:
            warnings.append("The PDF was damaged and has been repaired; some text may be missing.")

        page_count = pdf.page_count
        for page in pdf:
            page_number = page.number + 1  # PyMuPDF counts from 0, people count from 1
            blocks_before = len(blocks)
            for _x0, _y0, _x1, _y1, text, _block_no, block_type in page.get_text("blocks"):
                if block_type != TEXT_BLOCK:
                    continue
                text = text.strip()
                if not text:  # Word puts empty " \n" blocks between paragraphs
                    continue
                blocks.append(TextBlock(order=len(blocks), page=page_number, text=text))

            if len(blocks) == blocks_before:  # this page added no text
                if page.get_images():
                    scanned_pages.append(page_number)
                    warnings.append(f"Page {page_number} has images but no text; it may be scanned (OCR needed).")
                else:
                    warnings.append(f"Page {page_number} has no text (blank page).")

    if page_count > 0 and not blocks:
        if scanned_pages:
            warnings.append("No text layer found: this PDF looks scanned. OCR is needed before it can be searched.")
        else:
            warnings.append("No text found on any page.")

    for warning in warnings:
        logger.warning("%s: %s", path.name, warning)

    return ExtractedDocument(
        source_name=path.name,
        file_type="pdf",
        page_count=page_count,
        blocks=blocks,
        warnings=warnings,
    )


if __name__ == "__main__":
    import sys

    from app.config import get_settings
    from app.logging_config import configure_logging

    configure_logging()  # so the warnings logged above are shown with time and module
    regulations_dir = get_settings().regulations_dir
    file_name = sys.argv[1] if len(sys.argv) > 1 else "MiFID_II_2023_v1.pdf"

    document = extract_pdf(regulations_dir / file_name)
    print(f"{document.source_name}: {document.page_count} pages, {len(document.blocks)} blocks")
    for warning in document.warnings:
        print(f"  WARNING: {warning}")
    for block in document.blocks:
        first_line = block.text.splitlines()[0]
        print(f"  [{block.order:>2}] page {block.page}  {first_line[:70]}")

    print()
    print("All PDFs (blocks per page):")
    for path in sorted(regulations_dir.glob("*.pdf")):
        document = extract_pdf(path)
        per_page = [len(document.blocks_on_page(n)) for n in range(1, document.page_count + 1)]
        warnings = f"  warnings: {document.warnings}" if document.warnings else ""
        print(f"  {path.name:<32} {document.page_count} pages  {str(per_page):<12}{warnings}")