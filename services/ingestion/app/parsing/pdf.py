"""PDF text extraction with PyMuPDF, page by page.

Each PDF page is read separately, and every text block keeps its page
number. We never join the pages first: once joined, nobody can tell where
page 1 ended, and citations like "page 2" become impossible.

PyMuPDF splits a page into "blocks", usually one per paragraph. Each block
comes as a tuple: (x0, y0, x1, y1, text, block_number, block_type), where
block_type 0 is text and 1 is an image.

Try it from services/ingestion:
    python -m app.parsing.pdf                       (MiFID_II_2023_v1.pdf)
    python -m app.parsing.pdf EMIR_2021_v2.pdf      (any PDF in data/regulations)
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.parsing.models import ExtractedDocument, TextBlock

TEXT_BLOCK = 0  # block_type value for text (1 means image)


def extract_pdf(path: Path) -> ExtractedDocument:
    """Read every page of a PDF and return its text blocks with page numbers.

    Blocks keep PyMuPDF's order within a page, and pages are read in order,
    so `order` follows the document from start to end. Only the whitespace
    around each block is removed; everything else is kept exactly as
    extracted (Phase 4 does the cleaning).
    """
    blocks: list[TextBlock] = []
    with pymupdf.open(path) as pdf:
        page_count = pdf.page_count
        for page in pdf:
            page_number = page.number + 1  # PyMuPDF counts from 0, people count from 1
            for _x0, _y0, _x1, _y1, text, _block_no, block_type in page.get_text("blocks"):
                if block_type != TEXT_BLOCK:
                    continue
                text = text.strip()
                if not text:  # Word puts empty " \n" blocks between paragraphs
                    continue
                blocks.append(TextBlock(order=len(blocks), page=page_number, text=text))

    return ExtractedDocument(
        source_name=path.name,
        file_type="pdf",
        page_count=page_count,
        blocks=blocks,
    )


if __name__ == "__main__":
    import sys

    from app.config import get_settings

    regulations_dir = get_settings().regulations_dir
    file_name = sys.argv[1] if len(sys.argv) > 1 else "MiFID_II_2023_v1.pdf"

    document = extract_pdf(regulations_dir / file_name)
    print(f"{document.source_name}: {document.page_count} pages, {len(document.blocks)} blocks")
    for block in document.blocks:
        first_line = block.text.splitlines()[0]
        print(f"  [{block.order:>2}] page {block.page}  {first_line[:70]}")

    print()
    print("All PDFs (blocks per page):")
    for path in sorted(regulations_dir.glob("*.pdf")):
        document = extract_pdf(path)
        per_page = [len(document.blocks_on_page(n)) for n in range(1, document.page_count + 1)]
        print(f"  {path.name:<32} {document.page_count} pages  {per_page}")