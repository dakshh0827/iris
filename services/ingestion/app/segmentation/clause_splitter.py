"""Clause splitter (Phase 4): one chunk per clause, the way regulations are cited.

A clause starts at a block whose first line is:
  - "Paragraph N. Title"   (all our PDFs)           -> clause_ref "Paragraph N"
  - "QN: question"         (CFTC_2022.txt FAQ)       -> clause_ref "QN"
  - an HTML heading        ("4. Timing of Submission") -> "Section 4", or the heading text
Every following block, up to the next clause start, belongs to that clause.
So bullet lists stay with their lead-in (ESMA Paragraph 3), and a clause
that runs onto the next page gets page_start=1, page_end=2.

The pattern must match at the start of a block, so "see Paragraph 5." inside
a sentence never starts a new clause.
"""

from __future__ import annotations

import hashlib
import re

from app.parsing.models import TextBlock
from app.segmentation.cleaning import clean_text
from app.segmentation.models import Chunk

PARAGRAPH = re.compile(r"^Paragraph\s+(\d+)\.\s+(.+)$")
QUESTION = re.compile(r"^Q(\d+):\s*(.+)$")
NUMBERED_HEADING = re.compile(r"^(\d+)\.\s+(.+)$")


def text_hash(text: str) -> str:
    """SHA-256 of the cleaned text. Same text -> same hash, in every version of a document."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clause_start(block: TextBlock) -> tuple[str, str] | None:
    """Return (clause_ref, heading) if this block starts a clause, else None."""
    first_line = clean_text(block.text).split("\n")[0]
    if match := PARAGRAPH.match(first_line):
        return f"Paragraph {match.group(1)}", match.group(2).strip()
    if match := QUESTION.match(first_line):
        return f"Q{match.group(1)}", match.group(2).strip()
    if block.kind == "heading" and block.anchor is None and block.line_start is None:  # an HTML heading
        if match := NUMBERED_HEADING.match(first_line):
            return f"Section {match.group(1)}", match.group(2).strip()
        return first_line, first_line
    return None


def block_text(block: TextBlock) -> str:
    text = clean_text(block.text)
    return f"- {text}" if block.kind == "list_item" else text


def make_chunk(ordinal: int, clause_ref: str, heading: str | None, blocks: list[TextBlock],
               embedded_source: str | None = None) -> Chunk:
    """Build one chunk from its blocks, with the location taken from those blocks."""
    text = "\n".join(t for t in (block_text(b) for b in blocks) if t)
    pages = [b.page for b in blocks if b.page is not None]
    anchors = [b.anchor for b in blocks if b.anchor is not None]
    starts = [b.line_start for b in blocks if b.line_start is not None]
    ends = [b.line_end for b in blocks if b.line_end is not None]
    return Chunk(
        ordinal=ordinal, clause_ref=clause_ref, heading=heading, text=text, text_hash=text_hash(text),
        page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
        anchor=anchors[0] if anchors else None,
        line_start=min(starts) if starts else None, line_end=max(ends) if ends else None,
        foreign_content=embedded_source is not None, embedded_source=embedded_source,
    )


def split_clauses(blocks: list[TextBlock], first_ordinal: int = 0,
                  embedded_source: str | None = None) -> list[Chunk] | None:
    """Split blocks into clause chunks. Returns None if no clause start is found."""
    groups: list[tuple[str, str | None, list[TextBlock]]] = []
    preamble: list[TextBlock] = []
    for block in blocks:
        start = clause_start(block)
        if start is not None:
            groups.append((start[0], start[1], [block]))
        elif groups:
            groups[-1][2].append(block)
        else:
            preamble.append(block)
    if not groups:
        return None

    chunks: list[Chunk] = []
    # Text before the first clause is kept, unless it is only headings (a document title).
    if any(block.kind != "heading" for block in preamble):
        chunks.append(make_chunk(first_ordinal, "Preamble", preamble[0].heading, preamble, embedded_source))
    for clause_ref, heading, group in groups:
        is_html_heading_only = len(group) == 1 and group[0].kind == "heading" and group[0].anchor is None \
            and group[0].line_start is None
        if is_html_heading_only:
            continue  # e.g. the FCA <h1> title with no text of its own
        chunks.append(make_chunk(first_ordinal + len(chunks), clause_ref, heading, group, embedded_source))
    return chunks