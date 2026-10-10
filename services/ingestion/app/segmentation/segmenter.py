"""segment(): ExtractedDocument -> SegmentedDocument (Phase 4).

  1. Integrity check: split off documents pasted inside this one.
  2. For each part: clause splitter; if it finds no clauses, the fallback chunker.
  3. Mark chunks of pasted documents as foreign content, and say so in a warning.

Try it from services/ingestion:   python -m app.segmentation.segmenter
(segments all 10 files in data/regulations and prints a summary)
"""

from __future__ import annotations

import logging

from app.parsing.models import ExtractedDocument
from app.segmentation.cleaning import clean_text
from app.segmentation.clause_splitter import split_clauses
from app.segmentation.fallback_chunker import chunk_blocks
from app.segmentation.integrity import split_embedded
from app.segmentation.models import Chunk, SegmentedDocument

logger = logging.getLogger(__name__)


def segment(document: ExtractedDocument) -> SegmentedDocument:
    chunks: list[Chunk] = []
    warnings: list[str] = []
    methods: set[str] = set()

    for part in split_embedded(document.blocks, document.source_name):
        if part.embedded_source is not None:
            title = " / ".join(clean_text(block.text) for block in part.banner)
            page = part.banner[0].page
            where = f" on page {page}" if page is not None else ""
            warnings.append(f"Another document is pasted in{where}: '{title}'. "
                            f"Its text is marked as foreign content from {part.embedded_source}.")
        if not part.blocks:
            continue
        part_chunks = split_clauses(part.blocks, len(chunks), part.embedded_source)
        if part_chunks is None:
            part_chunks = chunk_blocks(part.blocks, len(chunks), part.embedded_source)
            methods.add("fallback")
        else:
            methods.add("clauses")
        chunks.extend(part_chunks)

    method = "none" if not methods else methods.pop() if len(methods) == 1 else "mixed"
    if not chunks:
        warnings.append("No chunks: the document has no text.")
    for warning in warnings:
        logger.warning("%s: %s", document.source_name, warning)
    return SegmentedDocument(source_name=document.source_name, method=method, chunks=chunks, warnings=warnings)


def describe(chunk: Chunk) -> str:
    """Short location, e.g. 'p.1-2', '#p4', 'lines 3-4'."""
    if chunk.page_start is not None:
        pages = str(chunk.page_start) if chunk.page_start == chunk.page_end else f"{chunk.page_start}-{chunk.page_end}"
        return f"p.{pages}"
    if chunk.anchor is not None:
        return f"#{chunk.anchor}"
    if chunk.line_start is not None:
        return f"lines {chunk.line_start}-{chunk.line_end}"
    return "-"


if __name__ == "__main__":
    import sys

    from app.config import get_settings
    from app.intake.validation import SNIFF_SIZE, check_file_type
    from app.logging_config import configure_logging
    from app.parsing.base import extract

    configure_logging()
    logging.getLogger().setLevel(logging.ERROR)  # the table below shows the warnings
    folder = get_settings().regulations_dir
    names = sys.argv[1:] or sorted(path.name for path in folder.iterdir())
    for name in names:
        path = folder / name
        with path.open("rb") as file:
            file_type = check_file_type(name, file.read(SNIFF_SIZE))
        result = segment(extract(path, file_type))
        foreign = sum(chunk.foreign_content for chunk in result.chunks)
        print(f"{name:<30} {len(result.chunks):>2} chunks ({result.method}), {foreign} foreign")
        for warning in result.warnings:
            print(f"    WARNING: {warning}")
        if len(names) == 1:
            for chunk in result.chunks:
                flag = f"  [foreign: {chunk.embedded_source}]" if chunk.foreign_content else ""
                print(f"    {chunk.ordinal:>2} {chunk.clause_ref:<12} {describe(chunk):<11} {chunk.heading}{flag}")
        else:
            print("    " + ", ".join(f"{c.clause_ref} {describe(c)}" for c in result.chunks))