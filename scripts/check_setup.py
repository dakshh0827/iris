"""Phase 1, Step 1: check that the development environment and dataset are ready.

Run from the project root (the folder that contains `data/` and `scripts/`):

    python scripts/check_setup.py

This script uses only the Python standard library, so it works before any
packages are installed. It does not modify anything; it only reads and reports.
Exit code 0 means every required check passed, 1 means at least one failed.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Python versions we support. 3.11 matches the Docker base image planned for
# Phase 12 (python:3.11-slim); 3.12 also works with every library we will use.
SUPPORTED_PYTHON = {(3, 11), (3, 12)}

# The project root is the parent of the `scripts/` folder this file lives in,
# so the script works no matter which directory you run it from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# Folders that must exist inside data/ (taken from the supplied dataset.zip).
REQUIRED_DATA_DIRS = ["regulations", "metadata", "schema", "rules", "trades", "queries"]

# File types the ingestion pipeline accepts. Anything else in regulations/
# is reported, because the pipeline will reject it.
SUPPORTED_EXTENSIONS = {".pdf", ".html", ".txt"}

# Exact column names found in data/metadata/doc_metadata.csv.
EXPECTED_METADATA_COLUMNS = [
    "document_id",
    "file_name",
    "regulator",
    "jurisdiction",
    "effective_date",
    "category",
    "version",
    "upload_timestamp",
    "parent_doc_id",
]


def report(ok: bool, message: str) -> bool:
    """Print one check result with a PASS/FAIL label and return `ok`."""
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {message}")
    return ok


def check_python_version() -> bool:
    """Return True if the running Python version is one we support."""
    major, minor = sys.version_info[:2]
    ok = (major, minor) in SUPPORTED_PYTHON
    return report(ok, f"Python version is {major}.{minor} (need 3.11 or 3.12)")


def check_virtualenv() -> bool:
    """Return True if Python is running inside a virtual environment.

    Inside a venv, `sys.prefix` points at the venv folder while
    `sys.base_prefix` still points at the system Python, so they differ.
    """
    in_venv = sys.prefix != sys.base_prefix
    return report(in_venv, f"Virtual environment active (sys.prefix = {sys.prefix})")


def check_data_dirs() -> bool:
    """Return True if data/ and all its expected sub-folders exist."""
    all_ok = report(DATA_DIR.is_dir(), f"Data folder exists at {DATA_DIR}")
    if not DATA_DIR.is_dir():
        return False
    for name in REQUIRED_DATA_DIRS:
        all_ok &= report((DATA_DIR / name).is_dir(), f"data/{name}/ exists")
    return all_ok


def list_regulation_files() -> tuple[bool, set[str]]:
    """Print every file in data/regulations/ and return (ok, set of file names)."""
    reg_dir = DATA_DIR / "regulations"
    if not reg_dir.is_dir():
        return False, set()

    files = sorted(p for p in reg_dir.iterdir() if p.is_file())
    print(f"\nRegulation files found in data/regulations/ ({len(files)}):")
    unsupported: list[str] = []
    for path in files:
        size_kb = path.stat().st_size / 1024
        ext = path.suffix.lower()
        marker = "" if ext in SUPPORTED_EXTENSIONS else "  <-- unsupported type"
        if marker:
            unsupported.append(path.name)
        print(f"  {path.name:<34} {ext:<6} {size_kb:>9.1f} KB{marker}")
    print()

    ok = report(len(files) > 0, "At least one regulation file is present")
    ok &= report(not unsupported, f"All regulation files are PDF/HTML/TXT (unsupported: {unsupported or 'none'})")
    return ok, {p.name for p in files}


def check_metadata(regulation_names: set[str]) -> bool:
    """Check doc_metadata.csv columns and cross-check it against data/regulations/."""
    meta_path = DATA_DIR / "metadata" / "doc_metadata.csv"
    if not report(meta_path.is_file(), "data/metadata/doc_metadata.csv exists"):
        return False

    # utf-8-sig silently drops a byte-order mark if a spreadsheet tool added one.
    with meta_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = list(reader)

    ok = report(
        columns == EXPECTED_METADATA_COLUMNS,
        f"Metadata columns match the expected list ({len(columns)} columns)",
    )
    if columns != EXPECTED_METADATA_COLUMNS:
        print(f"       found:    {columns}")
        print(f"       expected: {EXPECTED_METADATA_COLUMNS}")
        return False

    listed = {row["file_name"] for row in rows}
    print(f"       {len(rows)} metadata rows")
    missing_files = sorted(listed - regulation_names)
    unlisted_files = sorted(regulation_names - listed)
    ok &= report(not missing_files, f"Every metadata row points to an existing file (missing: {missing_files or 'none'})")
    # A file without metadata is allowed (metadata is optional), so this is a warning, not a failure.
    if unlisted_files:
        print(f"[WARN] Files with no metadata row (will need metadata at upload time): {unlisted_files}")
    return ok


def main() -> int:
    print(f"Project root: {PROJECT_ROOT}\n")
    results = [check_python_version(), check_virtualenv(), check_data_dirs()]
    files_ok, names = list_regulation_files()
    results.append(files_ok)
    results.append(check_metadata(names))

    passed = all(results)
    print("\nSetup check:", "ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED (see FAIL lines above)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
