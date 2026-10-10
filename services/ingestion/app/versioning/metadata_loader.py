"""Load data/metadata/doc_metadata.csv and resolve metadata for an upload (Phase 5).

Facts about the CSV (checked against the file):
  - effective_date is dd-mm-yy: "01-07-23" is 1 July 2023, not 7 January.
  - The regulator "Dodd-Frank" is written with a non-breaking hyphen (U+2011); we clean it to "Dodd-Frank".
  - parent_doc_id points to another row's document_id (2 -> 1, 4 -> 3, 9 -> 8).

Resolution order for each field: the upload form, then the CSV row with the
same file name, then empty. We never guess a regulator or a date: an empty
field gets a warning instead.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path

from app.config import get_settings
from app.segmentation.cleaning import clean_text

FIELDS = ("regulator", "jurisdiction", "category", "effective_date", "version_label")


@dataclass(frozen=True)
class DocMetadata:
    regulator: str | None = None
    jurisdiction: str | None = None
    category: str | None = None
    effective_date: date | None = None
    version_label: str | None = None
    source_doc_id: int | None = None
    parent_file_name: str | None = None


def parse_dd_mm_yy(value: str) -> date:
    """'01-07-23' -> date(2023, 7, 1). Raises ValueError for anything else."""
    return datetime.strptime(value.strip(), "%d-%m-%y").date()


def load_metadata_csv(path: Path | None = None) -> dict[str, DocMetadata]:
    """Return {file name: DocMetadata} for every row of the CSV."""
    path = path or get_settings().metadata_csv
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    names_by_id = {row["document_id"].strip(): row["file_name"].strip() for row in rows}
    result: dict[str, DocMetadata] = {}
    for row in rows:
        parent_id = row["parent_doc_id"].strip()
        result[row["file_name"].strip()] = DocMetadata(
            regulator=clean_text(row["regulator"]) or None,
            jurisdiction=row["jurisdiction"].strip() or None,
            category=row["category"].strip() or None,
            effective_date=parse_dd_mm_yy(row["effective_date"]) if row["effective_date"].strip() else None,
            version_label=row["version"].strip() or None,
            source_doc_id=int(row["document_id"]),
            parent_file_name=names_by_id.get(parent_id) if parent_id else None,
        )
    return result


def resolve_metadata(file_name: str, form: DocMetadata | None = None,
                     csv_rows: dict[str, DocMetadata] | None = None) -> tuple[DocMetadata, list[str]]:
    """Merge form and CSV metadata for one file. Returns (metadata, warnings)."""
    form = form or DocMetadata()
    row = (csv_rows if csv_rows is not None else load_metadata_csv()).get(file_name, DocMetadata())
    merged = DocMetadata(**{
        f.name: getattr(form, f.name) if getattr(form, f.name) is not None else getattr(row, f.name)
        for f in fields(DocMetadata)
    })
    missing = [name for name in FIELDS if getattr(merged, name) is None]
    warnings = [f"No metadata for: {', '.join(missing)}. These fields are left empty."] if missing else []
    return merged, warnings


if __name__ == "__main__":
    for name, meta in load_metadata_csv().items():
        print(f"{name:<30} {meta.regulator:<11} {meta.jurisdiction:<3} {meta.effective_date}  "
              f"{meta.version_label:<5} parent={meta.parent_file_name}")
