"""Family keys: which uploads are versions of the same document.

EMIR_2021_v1.pdf and EMIR_2021_v2.pdf are two versions of one regulation.
They share a "family key", EMIR_2021, so Phase 5 can store v2 as a new
version of the existing document instead of a new, unrelated one.

The family key also names the folder an upload is stored in (Step 7):
data/uploads/{family_key}/{version_id}/.

This file only derives the key from the file name. Phase 5 adds the better
sources: a key given in the upload form, then the metadata CSV. The file name
is the last fallback.

Try it from services/ingestion:   python -m app.intake.families
"""

from __future__ import annotations

import re
from pathlib import PurePath

from app.intake.validation import safe_file_name

# A version suffix at the end of the name: "_v2", "-v1.1", "_V3" (any case).
# It must follow "_" or "-", so a name like "Draftv2" is left alone.
VERSION_SUFFIX = re.compile(r"[_-][vV]\d+(?:\.\d+)*$")


def derive_family_key(file_name: str) -> str:
    """Return the family key for a file name, e.g. "EMIR_2021_v2.pdf" -> "EMIR_2021".

    Steps:
      1. make the name safe (Step 4), because the key becomes a folder name
      2. drop the extension: "EMIR_2021_v2.pdf" -> "EMIR_2021_v2"
      3. drop a version suffix at the end: "EMIR_2021_v2" -> "EMIR_2021"
    The letter case is kept ("MiFID_II_2023"), as in the architecture document.
    If removing the suffix would leave nothing (a file called "v2.pdf"), the
    name without extension is used instead.
    Raises InvalidFileName (from safe_file_name) if the name has nothing usable.
    """
    stem = PurePath(safe_file_name(file_name)).stem
    family_key = VERSION_SUFFIX.sub("", stem).rstrip("._-")
    return family_key or stem


if __name__ == "__main__":
    from app.config import get_settings

    families: dict[str, list[str]] = {}
    for path in sorted(get_settings().regulations_dir.iterdir()):
        families.setdefault(derive_family_key(path.name), []).append(path.name)

    for family_key, names in families.items():
        print(f"{family_key:<28} {', '.join(names)}")
    print()
    print(f"{sum(len(names) for names in families.values())} files in {len(families)} families")