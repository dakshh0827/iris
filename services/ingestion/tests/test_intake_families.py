"""Tests for app/intake/families.py (Step 6)."""

from __future__ import annotations

import csv

import pytest

from app.config import get_settings
from app.errors import InvalidFileName
from app.intake.families import derive_family_key


@pytest.mark.parametrize(
    ("file_name", "family_key"),
    [
        ("CFTC_2022.txt", "CFTC_2022"),
        ("Dodd_Frank_2022.pdf", "Dodd_Frank_2022"),
        ("EMIR_2021_v1.pdf", "EMIR_2021"),
        ("EMIR_2021_v2.pdf", "EMIR_2021"),
        ("ESMA_TRADE_REPORTING_2022.pdf", "ESMA_TRADE_REPORTING_2022"),
        ("FCA_Guidance_2023.html", "FCA_Guidance_2023"),
        ("MiFID_II_2023_v1.pdf", "MiFID_II_2023"),
        ("MiFID_II_2023_v2.pdf", "MiFID_II_2023"),
        ("SEC_GUIDANCE_2023_v1.pdf", "SEC_GUIDANCE_2023"),
        ("SEC_GUIDANCE_2023_v2.pdf", "SEC_GUIDANCE_2023"),
    ],
)
def test_supplied_file_names(file_name: str, family_key: str) -> None:
    assert derive_family_key(file_name) == family_key


def test_versions_in_the_metadata_share_a_family() -> None:
    """Cross-check with doc_metadata.csv: a row with parent_doc_id is a newer
    version of that parent, so both files must get the same family key."""
    with get_settings().metadata_csv.open(encoding="utf-8", newline="") as file:
        rows = {row["document_id"]: row for row in csv.DictReader(file)}

    children = [row for row in rows.values() if row["parent_doc_id"]]
    assert len(children) == 3  # MiFID II v2, EMIR v2, SEC Guidance v2
    for child in children:
        parent = rows[child["parent_doc_id"]]
        assert derive_family_key(child["file_name"]) == derive_family_key(parent["file_name"])


@pytest.mark.parametrize(
    ("file_name", "family_key"),
    [
        ("Rules-v1.2.pdf", "Rules"),           # hyphen and a dotted version
        ("Rules_V3.txt", "Rules"),             # capital V
        ("Draftv2.pdf", "Draftv2"),            # no separator before v: not a version
        ("v2.pdf", "v2"),                      # nothing would be left, so keep the name
        ("Guide_v2_final.pdf", "Guide_v2_final"),  # the version is not at the end
        ("../../EMIR_2021_v3.pdf", "EMIR_2021"),   # the name is made safe first
        ("My Rules (2024)_v2.pdf", "My_Rules_2024"),
    ],
)
def test_other_names(file_name: str, family_key: str) -> None:
    assert derive_family_key(file_name) == family_key


def test_unusable_name_is_rejected() -> None:
    with pytest.raises(InvalidFileName):
        derive_family_key("../..")