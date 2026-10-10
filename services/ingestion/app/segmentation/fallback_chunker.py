"""Fallback chunker (Phase 4): for text with no clause structure.

Cuts the text into windows of whole sentences, at most MAX_WORDS words each,
and repeats about OVERLAP_WORDS words from the end of one window at the start
of the next, so a sentence near a cut is never only half in a chunk.
We count words, not model tokens: close enough for sizing, and no extra library.
"""

from __future__ import annotations

import re

from app.parsing.models import TextBlock
from app.segmentation.cleaning import clean_text
from app.segmentation.clause_splitter import text_hash
from app.segmentation.models import Chunk

MAX_WORDS = 180
OVERLAP_WORDS = 30
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def sentences_of(block: TextBlock) -> list[str]:
    """Split one block's cleaned text into sentences; very long ones are cut into MAX_WORDS pieces."""
    pieces: list[str] = []
    for sentence in SENTENCE_BREAK.split(clean_text(block.text)):
        words = sentence.split()
        for start in range(0, len(words), MAX_WORDS):
            if words[start:start + MAX_WORDS]:
                pieces.append(" ".join(words[start:start + MAX_WORDS]))
    return pieces


def chunk_blocks(blocks: list[TextBlock], first_ordinal: int = 0,
                 embedded_source: str | None = None) -> list[Chunk]:
    """Make overlapping sentence windows. Each chunk's location covers the blocks it used."""
    items = [(sentence, block) for block in blocks for sentence in sentences_of(block)]
    chunks: list[Chunk] = []
    start = 0
    while start < len(items):
        end, words = start, 0
        while end < len(items) and (end == start or words + len(items[end][0].split()) <= MAX_WORDS):
            words += len(items[end][0].split())
            end += 1
        window = items[start:end]
        used = [block for _sentence, block in window]
        text = " ".join(sentence for sentence, _block in window)
        pages = [b.page for b in used if b.page is not None]
        anchors = [b.anchor for b in used if b.anchor is not None]
        starts = [b.line_start for b in used if b.line_start is not None]
        ends = [b.line_end for b in used if b.line_end is not None]
        chunks.append(Chunk(
            ordinal=first_ordinal + len(chunks), clause_ref=f"Part {len(chunks) + 1}",
            heading=used[0].heading, text=text, text_hash=text_hash(text),
            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
            anchor=anchors[0] if anchors else None,
            line_start=min(starts) if starts else None, line_end=max(ends) if ends else None,
            foreign_content=embedded_source is not None, embedded_source=embedded_source,
        ))
        if end >= len(items):
            break
        # Step back over the last sentences (up to OVERLAP_WORDS words) to start the next window.
        next_start, overlap = end, 0
        while next_start - 1 > start and overlap + len(items[next_start - 1][0].split()) <= OVERLAP_WORDS:
            next_start -= 1
            overlap += len(items[next_start][0].split())
        start = next_start
    return chunks