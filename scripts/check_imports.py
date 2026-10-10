"""Phase 1, Step 2: confirm the pinned libraries are installed and actually work.

Run from the project root, with the virtual environment active:

    python scripts/check_imports.py

For each library it prints the installed version next to the pinned version,
then does one tiny real task with the document libraries on the supplied data.
Exit code 0 means everything is fine, 1 means something is missing or wrong.
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = PROJECT_ROOT / "services" / "ingestion" / "requirements.txt"
REGULATIONS_DIR = PROJECT_ROOT / "data" / "regulations"


def read_pins(path: Path) -> dict[str, str]:
    """Return {package name: pinned version} from a requirements file.

    Lines look like 'uvicorn[standard]==0.54.0   # comment'. We drop the
    comment and the [extras] part, then split on '=='.
    """
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        name = name.split("[", 1)[0].strip()
        pins[name] = version.strip()
    return pins


def check_versions(pins: dict[str, str]) -> bool:
    """Compare each pinned version with the installed one. Return True if all match."""
    all_ok = True
    print(f"{'package':<20} {'pinned':<10} {'installed':<10} result")
    for name, pinned in pins.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = "MISSING"
        ok = installed == pinned
        all_ok &= ok
        print(f"{name:<20} {pinned:<10} {installed:<10} {'PASS' if ok else 'FAIL'}")
    return all_ok


def smoke_test_pdf() -> bool:
    """Open one real PDF with PyMuPDF and print its page count and first line."""
    import pymupdf  # imported here so a missing package shows as FAIL above, not a crash at the top

    pdf_path = REGULATIONS_DIR / "MiFID_II_2023_v1.pdf"
    try:
        with pymupdf.open(pdf_path) as doc:
            first_line = doc[0].get_text().strip().splitlines()[0]
            print(f"[PASS] PyMuPDF opened {pdf_path.name}: {doc.page_count} pages, first line: {first_line!r}")
            return True
    except Exception as exc:  # any failure here means the install is broken or the file is missing
        print(f"[FAIL] PyMuPDF could not read {pdf_path}: {exc}")
        return False


def smoke_test_html() -> bool:
    """Parse the FCA HTML file with BeautifulSoup's built-in html.parser."""
    from bs4 import BeautifulSoup

    html_path = REGULATIONS_DIR / "FCA_Guidance_2023.html"
    try:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        sections = soup.find_all("h2")
        print(f"[PASS] BeautifulSoup parsed {html_path.name}: {len(sections)} <h2> sections, title: {soup.title.get_text()!r}")
        return True
    except Exception as exc:
        print(f"[FAIL] BeautifulSoup could not parse {html_path}: {exc}")
        return False


def main() -> int:
    if sys.prefix == sys.base_prefix:
        print("[FAIL] No virtual environment is active. Activate .venv first.")
        return 1
    print(f"Python {sys.version.split()[0]} at {sys.executable}\n")

    versions_ok = check_versions(read_pins(REQUIREMENTS_FILE))
    print()
    if not versions_ok:
        print("Some packages are missing or have the wrong version. Run: python -m pip install -r services/ingestion/requirements.txt")
        return 1

    smoke_ok = smoke_test_pdf() & smoke_test_html()
    print("\nLibrary check:", "ALL CHECKS PASSED" if smoke_ok else "SOME CHECKS FAILED")
    return 0 if smoke_ok else 1


if __name__ == "__main__":
    sys.exit(main())