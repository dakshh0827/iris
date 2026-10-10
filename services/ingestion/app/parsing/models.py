"""Data models for extracted text (Phase 3).

Every extractor (PDF, HTML, TXT) returns the same shape, an ExtractedDocument:
an ordered list of TextBlocks, each remembering where it came from. Later
phases (chunking, citations) only ever see this shape, never PyMuPDF or
BeautifulSoup objects.

Where a block came from depends on the file type:
  PDF   page        (1, 2, 3 ...)
  HTML  anchor      (the element's id, e.g. "p4") and heading (its <h2> section)
  TXT   line_start / line_end   (1-based line numbers)

Try it from services/ingestion:   python -m app.parsing.models
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.intake.validation import FileType

BlockKind = Literal["heading", "paragraph", "list_item"]


class TextBlock(BaseModel):
    """One piece of text, in reading order, with its location in the source."""

    # frozen: a block can't be changed after it is created.
    # extra="forbid": a misspelled field name (e.g. "pages=") is an error, not silently ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")

    order: int = Field(ge=0, description="Position in the document: 0, 1, 2 ...")
    text: str = Field(description="The text exactly as extracted (cleaning is Phase 4)")
    kind: BlockKind = "paragraph"

    page: int | None = Field(default=None, ge=1, description="PDF page, starting at 1")
    anchor: str | None = Field(default=None, description="HTML element id, e.g. 'p4'")
    heading: str | None = Field(default=None, description="Heading of the section the block is in")
    line_start: int | None = Field(default=None, ge=1, description="TXT first line, starting at 1")
    line_end: int | None = Field(default=None, ge=1, description="TXT last line (inclusive)")

    @model_validator(mode="after")
    def check_block(self) -> TextBlock:
        """Rules that involve more than one field."""
        if not self.text.strip():
            raise ValueError("text must contain more than whitespace")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be given together")
        if self.line_start is not None and self.line_end < self.line_start:
            raise ValueError("line_end must not be before line_start")
        if self.page is None and self.anchor is None and self.heading is None and self.line_start is None:
            # Without a location we could never cite this text.
            raise ValueError("a block needs a location: page, anchor, heading or line numbers")
        return self


class ExtractedDocument(BaseModel):
    """All text blocks of one file, plus facts and warnings about the extraction."""

    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(description="The stored file name, e.g. 'MiFID_II_2023_v1.pdf'")
    file_type: FileType
    page_count: int | None = Field(default=None, ge=0, description="PDF only")
    blocks: list[TextBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="Problems worth showing, e.g. a blank page")

    @model_validator(mode="after")
    def check_document(self) -> ExtractedDocument:
        orders = [block.order for block in self.blocks]
        if orders != list(range(len(self.blocks))):
            raise ValueError("block order must be 0, 1, 2 ... with no gaps, in list order")
        if self.page_count is not None:
            for block in self.blocks:
                if block.page is not None and block.page > self.page_count:
                    raise ValueError(f"block {block.order} is on page {block.page}, "
                                     f"but the document has {self.page_count} pages")
        return self

    @property
    def text(self) -> str:
        """All block texts joined with blank lines, for a quick look or a search."""
        return "\n\n".join(block.text for block in self.blocks)

    def blocks_on_page(self, page: int) -> list[TextBlock]:
        """The blocks of one PDF page, in order."""
        return [block for block in self.blocks if block.page == page]


if __name__ == "__main__":
    # A hand-made example with the first two blocks of MiFID_II_2023_v1.pdf, page 1.
    # Step 2 builds this automatically from the real file.
    document = ExtractedDocument(
        source_name="MiFID_II_2023_v1.pdf",
        file_type="pdf",
        page_count=2,
        blocks=[
            TextBlock(order=0, kind="heading", page=1, text="Paragraph 1. Scope and Objective"),
            TextBlock(order=1, page=1, text="The Markets in Financial Instruments Directive ..."),
        ],
    )
    print(document.model_dump_json(indent=2, exclude_none=True))
    print()
    print("Blocks on page 1:", len(document.blocks_on_page(1)))

    print()
    try:
        TextBlock(order=0, text="Paragraph 7. Execution venue")  # no location at all
    except ValueError as error:
        print("Rejected:", error.errors()[0]["msg"])