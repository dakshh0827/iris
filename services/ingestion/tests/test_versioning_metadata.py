"""Tests for app/versioning/metadata_loader.py (Phase 5). No database needed."""

from __future__ import annotations

from datetime import date

import pytest

from app.versioning.metadata_loader import DocMetadata, load_metadata_csv, parse_dd_mm_yy, resolve_metadata


def test_dates_are_day_month_year() -> None:
    assert parse_dd_mm_yy("01-07-23") == date(2023, 7, 1)  # 1 July, not 7 January
    assert parse_dd_mm_yy("30-11-22") == date(2022, 11, 30)
    with pytest.raises(ValueError):
        parse_dd_mm_yy("2023-07-01")


def test_supplied_csv() -> None:
    rows = load_metadata_csv()
    assert len(rows) == 10
    mifid_v2 = rows["MiFID_II_2023_v2.pdf"]
    assert mifid_v2 == DocMetadata(regulator="MiFID II", jurisdiction="EU", category="Trade reporting",
                                   effective_date=date(2023, 7, 1), version_label="v2.0", source_doc_id=2,
                                   parent_file_name="MiFID_II_2023_v1.pdf")
    assert rows["Dodd_Frank_2022.pdf"].regulator == "Dodd-Frank"  # U+2011 cleaned to "-"
    parents = {name: meta.parent_file_name for name, meta in rows.items() if meta.parent_file_name}
    assert parents == {
        "MiFID_II_2023_v2.pdf": "MiFID_II_2023_v1.pdf",
        "EMIR_2021_v2.pdf": "EMIR_2021_v1.pdf",
        "SEC_GUIDANCE_2023_v2.pdf": "SEC_GUIDANCE_2023_v1.pdf",
    }


def test_form_values_win_and_csv_fills_the_rest() -> None:
    form = DocMetadata(regulator="ESMA", effective_date=date(2024, 1, 1))
    merged, warnings = resolve_metadata("MiFID_II_2023_v1.pdf", form)
    assert merged.regulator == "ESMA" and merged.effective_date == date(2024, 1, 1)
    assert merged.jurisdiction == "EU" and merged.version_label == "v1.0"
    assert warnings == []


def test_unknown_file_gets_empty_fields_and_a_warning() -> None:
    merged, warnings = resolve_metadata("my_notes.txt", DocMetadata(regulator="FCA"))
    assert merged == DocMetadata(regulator="FCA")  # nothing guessed
    assert warnings == ["No metadata for: jurisdiction, category, effective_date, version_label. "
                        "These fields are left empty."]
