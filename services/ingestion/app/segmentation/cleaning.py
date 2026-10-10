"""Text cleaning (Phase 4): make text consistent for search without changing its meaning.

What we change:
  - Unicode NFKC: "compatibility" characters become their plain form, e.g. the
    narrow no-break space U+202F and no-break space U+00A0 become a normal
    space, and the ligature "fi" (one character) becomes "f" + "i".
  - All hyphen and dash characters (U+2010 to U+2015, minus sign U+2212)
    become "-", so a search for "Dodd-Frank" finds "Dodd\u2011Frank".
  - Curly quotes become straight quotes.
  - Backticks around field names go, the name stays: `Clearing_House` -> Clearing_House.
  - Runs of spaces and tabs become one space.
  - A line break in the middle of a sentence (PDF line wrapping) is joined.

What we keep: numbers, dates, symbols such as >= (U+2265) and the pound sign,
field names, and line breaks after a sentence or before a bullet.

Try it from services/ingestion:   python -m app.segmentation.cleaning
"""

from __future__ import annotations

import re
import unicodedata

DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = {ord("\u201c"): '"', ord("\u201d"): '"', ord("\u201e"): '"',
          ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201a"): "'"}
CHARACTER_MAP = DASHES | QUOTES

BACKTICKED = re.compile(r"`([^`\n]+)`")
SPACES = re.compile(r"[ \t\f\v]+")
SENTENCE_END = (".", "?", "!", ":", ";")
BULLET_START = re.compile(r"^(?:[*\u2022-]\s|\d+[.)]\s|[QA]\d+:)")


def clean_text(text: str) -> str:
    """Return the cleaned text. Cleaning twice gives the same result as once."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(CHARACTER_MAP)
    text = BACKTICKED.sub(r"\1", text)

    lines = [SPACES.sub(" ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    result = lines[0]
    for line in lines[1:]:
        if result.endswith(SENTENCE_END) or BULLET_START.match(line):
            result += "\n" + line   # a real line break: keep it
        elif result.endswith("-"):
            result += line          # "non-" + "negative" -> "non-negative"
        else:
            result += " " + line    # PDF line wrap in the middle of a sentence
    return result


if __name__ == "__main__":
    samples = [
        "All derivative contracts executed after 1\u202fJanuary\u202f2021 must be reported within \none business day.",
        "Dodd\u2011Frank \u2013 Price\u202f\u2265\u202f0, fine of up to \u00a310,000",
        "include the field `Clearing_House` containing the LEI",
        "\u201cAmendment=Y\u201d and the \ufb01eld",
        "Validation Rules:\n* Price must be \u2265 0.\n* Quantity must be positive.",
    ]
    for sample in samples:
        print(ascii(sample))
        print("  ->", ascii(clean_text(sample)))