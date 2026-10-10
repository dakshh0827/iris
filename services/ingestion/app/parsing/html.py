"""HTML text extraction with BeautifulSoup and Python's built-in "html.parser".

An HTML page has no pages, so a block's location is:
  - anchor:  the id of the element it is in, e.g. "p4" (a link can point to
             FCA_Guidance_2023.html#p4), and
  - heading: the text of the last heading (<h1> ... <h6>) before it.

Why "html.parser" and not "lxml": FCA_Guidance_2023.html puts a <ul> list
inside <p id="p4">. That is not allowed in HTML, and lxml "fixes" it by moving
the list out of the paragraph, so the three reporting deadlines lose their
anchor. html.parser keeps the list where the author put it.

We walk the page from top to bottom. Block elements (<p>, <li>, <div>,
headings ...) start a new block; inline elements (<strong>, <a>, <span> ...)
just add their text to the current one. A paragraph that contains a list
becomes: the text before the list, one block per list item, then the text
after the list, all with the paragraph's anchor.

Encoding: we turn the bytes into text ourselves (decode_html) instead of
letting BeautifulSoup guess. If a guessing library such as charset-normalizer
happens to be installed, BeautifulSoup uses it, and broken bytes can come out
as different letters on different machines. Our rule is always the same: the
<meta charset> if it works, otherwise UTF-8, otherwise Windows-1252 (as for TXT).

Try it from services/ingestion:   python -m app.parsing.html
"""

from __future__ import annotations

import codecs
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.dammit import EncodingDetector
from bs4.element import PreformattedString

from app.parsing.models import BlockKind, ExtractedDocument, TextBlock
from app.parsing.txt import decode_text

logger = logging.getLogger(__name__)

# Removed completely before reading: code, styling and page furniture, not content.
REMOVED_TAGS = ["script", "style", "noscript", "template", "nav", "footer"]

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Elements that start a new block. Anything else (strong, em, a, span ...) is inline.
BLOCK_TAGS = HEADING_TAGS | {
    "p", "div", "section", "article", "main", "header", "aside", "blockquote", "pre",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "tr", "td", "th", "figure", "figcaption",
}

# Runs of HTML whitespace (space, tab, line breaks). A browser shows each run as
# one space. Unicode spaces such as U+202F are content, so they are left alone
# here; Phase 4 does the cleaning.
HTML_WHITESPACE = re.compile(r"[ \t\n\r\f]+")


class _BlockCollector:
    """Walks the HTML tree and collects TextBlocks in reading order."""

    def __init__(self, first_heading: str) -> None:
        self.blocks: list[TextBlock] = []
        self.parts: list[str] = []          # text of the block being built
        self.kind: BlockKind = "paragraph"
        self.anchor: str | None = None
        self.heading = first_heading        # until the first real heading

    def walk(self, element: Tag) -> None:
        for child in element.children:
            if isinstance(child, PreformattedString):  # comments, <!DOCTYPE ...>
                continue
            if isinstance(child, NavigableString):     # plain text
                self.parts.append(HTML_WHITESPACE.sub(" ", child))
                continue
            if not isinstance(child, Tag):
                continue
            if child.name == "br":
                self.parts.append("\n")
            elif child.name in BLOCK_TAGS:
                self.flush()  # text before this element is a block of its own
                saved = (self.kind, self.anchor)
                if child.name in HEADING_TAGS:
                    self.kind = "heading"
                elif child.name == "li":
                    self.kind = "list_item"
                # An element without an id keeps the id of the element around it,
                # so the <li> items inside <p id="p4"> get the anchor "p4".
                self.anchor = child.get("id") or self.anchor
                self.walk(child)
                self.flush()
                self.kind, self.anchor = saved
            else:
                self.walk(child)  # inline element: its text joins the current block

    def flush(self) -> None:
        """Turn the collected text into a block (if there is any text)."""
        text = "".join(self.parts)
        self.parts = []
        lines = (line.strip(" ") for line in text.split("\n"))
        text = "\n".join(line for line in lines if line)
        if not text.strip():
            return
        if self.kind == "heading":
            self.heading = text  # the blocks after it belong to this section
        self.blocks.append(TextBlock(
            order=len(self.blocks), kind=self.kind, text=text, anchor=self.anchor, heading=self.heading,
        ))


def decode_html(data: bytes) -> tuple[str, list[str]]:
    """Bytes to text: the declared <meta charset> first, then the same rules as TXT files."""
    declared = EncodingDetector.find_declared_encoding(data, is_html=True)
    if declared:
        try:
            if codecs.lookup(declared).name != "utf-8":  # UTF-8 is decode_text's first try anyway
                return data.decode(declared), []
        except (LookupError, UnicodeDecodeError):
            pass  # unknown name, or the bytes don't match it: fall back
    text, _encoding, warnings = decode_text(data)
    return text, warnings


def extract_html(path: Path) -> ExtractedDocument:
    """Read an HTML file and return its text blocks with anchors and headings."""
    html, warnings = decode_html(path.read_bytes())
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(REMOVED_TAGS):
        tag.decompose()

    # Text before the first heading is located by the page title, or the file name.
    title = soup.title.get_text() if soup.title else ""
    title = HTML_WHITESPACE.sub(" ", title).strip(" ")
    collector = _BlockCollector(first_heading=title or path.name)
    collector.walk(soup.body or soup)
    collector.flush()

    if not collector.blocks:
        warnings.append("No text found in the HTML.")
    for warning in warnings:
        logger.warning("%s: %s", path.name, warning)

    return ExtractedDocument(
        source_name=path.name,
        file_type="html",
        blocks=collector.blocks,
        warnings=warnings,
    )


if __name__ == "__main__":
    from app.config import get_settings
    from app.logging_config import configure_logging

    configure_logging()
    document = extract_html(get_settings().regulations_dir / "FCA_Guidance_2023.html")
    print(f"{document.source_name}: {len(document.blocks)} blocks, warnings: {document.warnings}")
    for block in document.blocks:
        anchor = f"#{block.anchor}" if block.anchor else "-"
        first_line = block.text.splitlines()[0]
        print(f"  [{block.order:>2}] {block.kind:<9} {anchor:<4} {first_line[:60]}")
