"""Data models for chunks (Phase 4).

A Chunk is the unit we index and cite: one regulation clause ("Paragraph 3"),
one HTML section ("Section 4", anchor #p4) or one FAQ pair ("Q5"). Each chunk
keeps where it came from, so search results can say "page 2" or "lines 3-4".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SegmentMethod = Literal["clauses", "fallback", "mixed", "none"]


class Chunk(BaseModel):
    """One citable piece of cleaned text with its source location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordinal: int = Field(ge=0, description="Position in the document: 0, 1, 2 ...")
    clause_ref: str = Field(description="How people cite it: 'Paragraph 3', 'Section 4', 'Q5', 'Part 2'")
    heading: str | None = None
    text: str = Field(description="Cleaned text, including the heading line")
    text_hash: str = Field(min_length=64, max_length=64, description="SHA-256 of text, in hex")

    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    anchor: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    foreign_content: bool = False          # text of another document pasted into this one
    embedded_source: str | None = None     # that other document's file name, e.g. "Dodd_Frank_2022.pdf"

    @model_validator(mode="after")
    def check_chunk(self) -> Chunk:
        if not self.text.strip():
            raise ValueError("a chunk needs text")
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must be given together")
        if self.page_start is not None and self.page_end < self.page_start:
            raise ValueError("page_end must not be before page_start")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be given together")
        if self.page_start is None and self.anchor is None and self.line_start is None and self.heading is None:
            raise ValueError("a chunk needs a location: pages, anchor, lines or heading")
        if self.foreign_content != (self.embedded_source is not None):
            raise ValueError("foreign_content and embedded_source go together")
        return self


class SegmentedDocument(BaseModel):
    """All chunks of one document, plus how they were made and any warnings."""

    model_config = ConfigDict(extra="forbid")

    source_name: str
    method: SegmentMethod
    chunks: list[Chunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_order(self) -> SegmentedDocument:
        if [chunk.ordinal for chunk in self.chunks] != list(range(len(self.chunks))):
            raise ValueError("chunk ordinals must be 0, 1, 2 ... in list order")
        return self