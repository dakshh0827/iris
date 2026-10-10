"""Plain-text (TXT) extraction with line numbers.

A text file has no pages and no tags, so a block's location is its line
numbers: CFTC_2022.txt "lines 3-4" is the Q1/A1 pair. A block is a group of
lines with no blank line in between (what people call a paragraph).

Two things make text files tricky:

  - Encoding. A file is only bytes; we must guess how they map to letters.
    We try UTF-8 (with or without the 3-byte BOM some editors add), then
    Windows-1252, the old default on Windows. Intake (Phase 2) only checked
    the first 8 KB, so a file can still turn out not to be UTF-8 further on.
  - Line endings. Windows ends lines with \\r\\n (CRLF, as in CFTC_2022.txt),
    Linux and macOS with \\n, very old Macs with \\r. We turn all of them into
    \\n before counting, so line numbers match what an editor shows.

A title followed by a line of dashes or equals signs (like the first two
lines of CFTC_2022.txt) is a heading. Its text becomes the heading of the
blocks after it, as with <h2> in HTML.

Try it from services/ingestion:   python -m app.parsing.txt
"""

from __future__ import annotations

import codecs
import logging
import re
from pathlib import Path

from app.parsing.models import ExtractedDocument, TextBlock

logger = logging.getLogger(__name__)

# A line made only of - or = characters (at least 3), used to underline a title.
UNDERLINE = re.compile(r"^\s*(-{3,}|={3,})\s*$")


def decode_text(data: bytes) -> tuple[str, str, list[str]]:
    """Turn bytes into text. Returns (text, encoding name, warnings)."""
    encoding = "utf-8"
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]  # the BOM is a marker, not part of the text
        encoding = "utf-8-sig"              # Python's name for "UTF-8 with a BOM"
    try:
        return data.decode("utf-8"), encoding, []
    except UnicodeDecodeError:
        pass

    not_utf8 = "The file is not valid UTF-8; it was read as Windows-1252."
    try:
        return data.decode("cp1252"), "cp1252", [not_utf8]
    except UnicodeDecodeError:
        # 5 byte values (0x81, 0x8D, 0x8F, 0x90, 0x9D) mean nothing in Windows-1252.
        # Keep going, but say so.
        replaced = "Some characters could not be decoded and were replaced with U+FFFD."
        return data.decode("cp1252", errors="replace"), "cp1252", [not_utf8, replaced]


def split_lines(text: str) -> list[str]:
    """Split text into lines, treating \\r\\n, \\r and \\n as one line break each.

    We don't use str.splitlines(): it also breaks at rare characters such as
    form feed or U+2028, and then our line numbers would not match an editor's.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def extract_txt(path: Path) -> ExtractedDocument:
    """Read a text file and return its blocks with line numbers."""
    text, _encoding, warnings = decode_text(path.read_bytes())
    lines = split_lines(text)

    blocks: list[TextBlock] = []
    heading: str | None = None
    group: list[str] = []  # lines of the block being built
    group_start = 0        # its first line number

    def finish_group(last_line: int) -> None:
        """Turn the collected lines into a block (if there are any)."""
        if group:
            blocks.append(TextBlock(order=len(blocks), text="\n".join(group).strip(), heading=heading,
                                    line_start=group_start, line_end=last_line))
            group.clear()

    for number, line in enumerate(lines, start=1):  # people count lines from 1
        if not line.strip():
            finish_group(last_line=number - 1)  # a blank line ends the block
        elif UNDERLINE.match(line) and group:
            # The line above this underline is a title, e.g. lines 1-2 of CFTC_2022.txt.
            title = group.pop()
            finish_group(last_line=number - 2)  # lines before the title, if any
            heading = title.strip()
            blocks.append(TextBlock(order=len(blocks), kind="heading", text=heading, heading=heading,
                                    line_start=number - 1, line_end=number))
        else:
            if not group:
                group_start = number
            group.append(line)
    finish_group(last_line=len(lines))

    if not blocks:
        warnings.append("No text found in the file.")
    for warning in warnings:
        logger.warning("%s: %s", path.name, warning)

    return ExtractedDocument(source_name=path.name, file_type="txt", blocks=blocks, warnings=warnings)


if __name__ == "__main__":
    from app.config import get_settings
    from app.logging_config import configure_logging

    configure_logging()
    path = get_settings().regulations_dir / "CFTC_2022.txt"
    data = path.read_bytes()
    _text, encoding, _warnings = decode_text(data)
    crlf_count = data.count(b"\r\n")
    print(f"{path.name}: {len(data)} bytes, encoding {encoding}, {crlf_count} CRLF line endings")

    document = extract_txt(path)
    print(f"{len(document.blocks)} blocks, warnings: {document.warnings}")
    for block in document.blocks:
        lines = f"lines {block.line_start}-{block.line_end}"
        first_line = block.text.splitlines()[0]
        print(f"  [{block.order:>2}] {block.kind:<9} {lines:<12} {first_line[:55]}")