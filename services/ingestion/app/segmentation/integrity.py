"""Integrity check (Phase 4): find other documents pasted inside a document.

EMIR_2021_v2.pdf contains, after its own Paragraph 7, the whole Dodd-Frank
document, introduced by two lines:
    Dodd-Frank Act - 2022 (Single version)
    File name: Dodd_Frank_2022.pdf
Its "Paragraph 1 ... 7" would clash with EMIR's own paragraphs and make the
EMIR v1 -> v2 comparison say "Dodd-Frank was added to EMIR". So we split the
document at such a banner and mark everything after it as foreign content.

We only trust an explicit "File name: <name>.pdf|.html|.txt" line. We never
guess a regulator or a source from the wording of the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsing.models import TextBlock

FILE_NAME_BANNER = re.compile(r"^\s*File\s*name:\s*(\S+\.(?:pdf|html?|txt))\s*$", re.IGNORECASE)
CLAUSE_LIKE = re.compile(r"^(?:Paragraph\s+\d+\.|Q\d+:|\d+\.\s)")


@dataclass
class Segment:
    """A run of blocks that belong to one document."""

    blocks: list[TextBlock]
    embedded_source: str | None = None        # None = the document's own text
    banner: list[TextBlock] = field(default_factory=list)


def is_title_line(block: TextBlock) -> bool:
    """A short single line that is not a clause and doesn't end like a sentence."""
    text = block.text.strip()
    return "\n" not in text and len(text) <= 120 and not text.endswith(".") and not CLAUSE_LIKE.match(text)


def split_embedded(blocks: list[TextBlock], source_name: str) -> list[Segment]:
    """Split blocks at "File name: ..." banners that name a different file."""
    segments = [Segment(blocks=[])]
    for block in blocks:
        match = FILE_NAME_BANNER.match(block.text)
        if match and match.group(1).lower() != source_name.lower():
            current = segments[-1]
            banner = [block]
            # A title line right above the banner belongs to the banner, not to our last clause.
            if current.blocks and is_title_line(current.blocks[-1]):
                banner.insert(0, current.blocks.pop())
            segments.append(Segment(blocks=[], embedded_source=match.group(1), banner=banner))
        else:
            segments[-1].blocks.append(block)
    return segments