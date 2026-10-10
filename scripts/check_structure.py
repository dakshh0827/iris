"""Phase 1, Step 3: check the ingestion service package skeleton.

Run from the project root, with the virtual environment active:

    python scripts/check_structure.py

It checks that every expected folder has an __init__.py, then imports each
package the same way the running service will, and prints its purpose.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = PROJECT_ROOT / "services" / "ingestion"

# Every Python package the ingestion service will have, in pipeline order.
EXPECTED_PACKAGES = [
    "app",
    "app.api",
    "app.intake",
    "app.parsing",
    "app.segmentation",
    "app.db",
    "app.repositories",
    "app.versioning",
    "app.indexing",
]


def check_files() -> bool:
    """Return True if each expected package folder contains an __init__.py."""
    all_ok = True
    for package in EXPECTED_PACKAGES:
        init_file = SERVICE_DIR / Path(*package.split(".")) / "__init__.py"
        ok = init_file.is_file()
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {init_file.relative_to(PROJECT_ROOT)}")
    tests_ok = (SERVICE_DIR / "tests").is_dir()
    print(f"[{'PASS' if tests_ok else 'FAIL'}] {(SERVICE_DIR / 'tests').relative_to(PROJECT_ROOT)}/ folder exists")
    return all_ok and tests_ok


def check_imports() -> bool:
    """Import every package and print the first line of its docstring."""
    # Python finds `app` only if services/ingestion is on its search path.
    # The service will be started from that folder, so we add it here to copy that.
    sys.path.insert(0, str(SERVICE_DIR))
    all_ok = True
    for package in EXPECTED_PACKAGES:
        try:
            module = importlib.import_module(package)
            purpose = (module.__doc__ or "(no docstring)").strip().splitlines()[0]
            print(f"[PASS] import {package:<18} {purpose}")
        except Exception as exc:  # report every broken package, not just the first
            all_ok = False
            print(f"[FAIL] import {package:<18} {type(exc).__name__}: {exc}")
    return all_ok


def main() -> int:
    if not SERVICE_DIR.is_dir():
        print(f"[FAIL] {SERVICE_DIR} does not exist. Create services/ingestion first.")
        return 1
    files_ok = check_files()
    print()
    imports_ok = check_imports()
    passed = files_ok and imports_ok
    print("\nStructure check:", "ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
