"""Tests for app/parsing/html.py (Phase 3, Steps 6 and 7; encoding fix after Phase 5).

The first tests use the real FCA_Guidance_2023.html. The others write tiny
HTML files into pytest's temporary folder to check one rule at a time.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.parsing.html import extract_html
from app.parsing.models import ExtractedDocument

FCA = get_settings().regulations_dir / "FCA_Guidance_2023.html"


def html_file(tmp_path: Path, body: str, title: str = "Test page") -> ExtractedDocument:
    """Write a small HTML page and extract it."""
    head = f"<title>{title}</title>" if title else ""
    path = tmp_path / "page.html"
    path.write_text(f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>", encoding="utf-8")
    return extract_html(path)


# --- The real FCA file -------------------------------------------------------

def test_fca_sections_and_anchors() -> None:
    document = extract_html(FCA)
    assert document.file_type == "html"
    assert document.page_count is None  # HTML has no pages
    assert document.warnings == []
    assert len(document.blocks) == 19
    headings = [block.text for block in document.blocks if block.kind == "heading"]
    assert len(headings) == 8  # the <h1> title and seven <h2> sections
    assert headings[1] == "1. Scope and Applicability"
    assert headings[7] == "7. Recent Amendments (2023\u201106\u201101)"
    anchors = [block.anchor for block in document.blocks if block.kind == "paragraph"]
    assert anchors == ["p1", "p2", "p3", "p4", "p4", "p5", "p6", "p7"]


def test_fca_section_4_keeps_its_three_deadlines() -> None:
    # The <ul> sits inside <p id="p4">. With lxml these items would be lost.
    blocks = [block for block in extract_html(FCA).blocks if block.anchor == "p4"]
    assert [(block.kind, block.text) for block in blocks] == [
        ("paragraph", "Trades must be reported to the FCA within the following timeframes:"),
        ("list_item", "Equities and bonds \u2013 end of the next business day."),
        ("list_item", "Derivatives \u2013 within 30 minutes of execution."),
        ("list_item", "FX \u2013 within 60 minutes of execution."),
        ("paragraph", "Late submissions are subject to a supervisory notice and may attract "
                      "a fine of up to \u00a310,000 per breach."),
    ]
    assert {block.heading for block in blocks} == {"4. Timing of Submission"}


def test_fca_has_no_css_and_keeps_special_characters() -> None:
    document = extract_html(FCA)
    assert "font-family" not in document.text  # the <style> block is gone
    p7 = [block for block in document.blocks if block.anchor == "p7"][0]
    assert p7.text.startswith("Effective 1\u202fJune\u202f2023, the FCA introduced a new field Execution_Venue ")
    assert p7.heading == "7. Recent Amendments (2023\u201106\u201101)"


# --- One rule at a time ------------------------------------------------------

def test_inline_tags_join_the_paragraph(tmp_path: Path) -> None:
    document = html_file(tmp_path, '<p id="a">The field <strong>Currency</strong> must be <a href="#x">valid</a>.</p>')
    assert [block.text for block in document.blocks] == ["The field Currency must be valid."]


def test_whitespace_is_collapsed_but_unicode_spaces_are_kept(tmp_path: Path) -> None:
    document = html_file(tmp_path, '<p id="a">\n    Effective 1\u202fJune\n    2023,\tnow.\n</p>')
    assert document.blocks[0].text == "Effective 1\u202fJune 2023, now."


def test_br_starts_a_new_line(tmp_path: Path) -> None:
    document = html_file(tmp_path, '<p id="a">Line one<br>Line two<br/></p>')
    assert document.blocks[0].text == "Line one\nLine two"


def test_code_styling_navigation_and_comments_are_removed(tmp_path: Path) -> None:
    body = (
        "<nav>Home | About</nav><script>alert(1)</script><style>p {color: red}</style>"
        '<!-- a comment --><h2>Rules</h2><p id="r1">Real text.</p><footer>Copyright</footer>'
    )
    document = html_file(tmp_path, body)
    assert [block.text for block in document.blocks] == ["Rules", "Real text."]


def test_headings_are_carried_to_the_following_blocks(tmp_path: Path) -> None:
    document = html_file(tmp_path, "<h2>Part A</h2><p>One</p><p>Two</p><h3>Part B</h3><ul><li>Three</li></ul>")
    assert [(block.kind, block.text, block.heading) for block in document.blocks] == [
        ("heading", "Part A", "Part A"),
        ("paragraph", "One", "Part A"),
        ("paragraph", "Two", "Part A"),
        ("heading", "Part B", "Part B"),
        ("list_item", "Three", "Part B"),
    ]


def test_text_before_any_heading_uses_the_title_or_file_name(tmp_path: Path) -> None:
    assert html_file(tmp_path, "<p>Intro</p>", title="My guide").blocks[0].heading == "My guide"
    assert html_file(tmp_path, "<p>Intro</p>", title="").blocks[0].heading == "page.html"


def test_own_id_wins_over_the_surrounding_id(tmp_path: Path) -> None:
    document = html_file(tmp_path, '<div id="sec1"><p>Plain</p><p id="x">Own id</p></div>')
    assert [block.anchor for block in document.blocks] == ["sec1", "x"]


def test_empty_page_gives_a_warning(tmp_path: Path) -> None:
    document = html_file(tmp_path, "<p>   </p><div>\u00a0</div>")
    assert document.blocks == []
    assert document.warnings == ["No text found in the HTML."]


def test_undecodable_bytes_give_a_warning(tmp_path: Path) -> None:
    path = tmp_path / "bad.html"
    # \x81 and \x8d are not valid UTF-8, and not valid Windows-1252 (our next try) either.
    path.write_bytes(b'<meta charset="utf-8"><p id="a">Bad \x81\x8d bytes</p>')
    document = extract_html(path)
    assert document.blocks[0].text == "Bad \ufffd\ufffd bytes"
    assert document.warnings == ["The file is not valid UTF-8; it was read as Windows-1252.",
                                 "Some characters could not be decoded and were replaced with U+FFFD."]


def test_declared_charset_is_used(tmp_path: Path) -> None:
    path = tmp_path / "latin.html"
    path.write_bytes(b'<meta charset="iso-8859-1"><p id="a">Caf\xe9 r\xe8gle</p>')
    document = extract_html(path)
    assert document.blocks[0].text == "Caf\u00e9 r\u00e8gle"
    assert document.warnings == []


def test_windows_1252_without_a_charset_is_read_with_a_warning(tmp_path: Path) -> None:
    path = tmp_path / "old.html"
    path.write_bytes(b'<p id="a">\x93Quoted\x94 \x96 dash</p>')
    document = extract_html(path)
    assert document.blocks[0].text == "\u201cQuoted\u201d \u2013 dash"
    assert document.warnings == ["The file is not valid UTF-8; it was read as Windows-1252."]
