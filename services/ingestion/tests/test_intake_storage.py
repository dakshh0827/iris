"""Tests for app/intake/storage.py (Step 7)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import EmptyFile, FileTooLarge
from app.intake.families import derive_family_key
from app.intake.hashing import sha256_of_file
from app.intake.storage import new_version_id, store_original

VERSION_ID = "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"


def all_files(folder: Path) -> list[Path]:
    """Every file under `folder`, at any depth."""
    return [path for path in folder.rglob("*") if path.is_file()]


def test_every_supplied_file_is_stored_unchanged(tmp_path: Path) -> None:
    for source in sorted(get_settings().regulations_dir.iterdir()):
        version_id = new_version_id()
        family_key = derive_family_key(source.name)
        with source.open("rb") as stream:
            stored = store_original(
                stream, family_key=family_key, version_id=version_id,
                file_name=source.name, uploads_dir=tmp_path,
            )
        assert stored.path == tmp_path / family_key / version_id / source.name
        assert stored.path.read_bytes() == source.read_bytes()
        assert stored.size_bytes == source.stat().st_size
        assert stored.sha256 == sha256_of_file(source)
    assert len(all_files(tmp_path)) == 10  # no .partial files left behind


def test_too_large_is_rejected_and_nothing_is_left(tmp_path: Path) -> None:
    with pytest.raises(FileTooLarge):
        store_original(
            io.BytesIO(b"x" * 101), family_key="Big", version_id=VERSION_ID,
            file_name="big.txt", uploads_dir=tmp_path, max_bytes=100,
        )
    assert list(tmp_path.iterdir()) == []  # the empty folders were removed too


def test_exactly_the_limit_is_stored(tmp_path: Path) -> None:
    stored = store_original(
        io.BytesIO(b"x" * 100), family_key="Edge", version_id=VERSION_ID,
        file_name="edge.txt", uploads_dir=tmp_path, max_bytes=100,
    )
    assert stored.size_bytes == 100


def test_empty_is_rejected_and_nothing_is_left(tmp_path: Path) -> None:
    with pytest.raises(EmptyFile):
        store_original(
            io.BytesIO(b""), family_key="Empty", version_id=VERSION_ID,
            file_name="empty.txt", uploads_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_existing_original_is_never_overwritten(tmp_path: Path) -> None:
    store_original(
        io.BytesIO(b"first"), family_key="Doc", version_id=VERSION_ID,
        file_name="doc.txt", uploads_dir=tmp_path,
    )
    with pytest.raises(FileExistsError):
        store_original(
            io.BytesIO(b"second"), family_key="Doc", version_id=VERSION_ID,
            file_name="doc.txt", uploads_dir=tmp_path,
        )
    assert (tmp_path / "Doc" / VERSION_ID / "doc.txt").read_bytes() == b"first"


class BrokenStream(io.BytesIO):
    """A stream that fails after the first read, like a dropped connection."""

    def __init__(self) -> None:
        super().__init__(b"")
        self.reads = 0

    def read(self, size: int | None = -1) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return b"half of a file"
        raise ConnectionError("upload interrupted")


def test_failure_in_the_middle_leaves_no_partial_file(tmp_path: Path) -> None:
    with pytest.raises(ConnectionError):
        store_original(
            BrokenStream(), family_key="Cut", version_id=VERSION_ID,
            file_name="cut.txt", uploads_dir=tmp_path,
        )
    assert all_files(tmp_path) == []


def test_unsafe_names_stay_inside_the_uploads_folder(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    stored = store_original(
        io.BytesIO(b"data"), family_key="../../outside", version_id=VERSION_ID,
        file_name="../../../evil.txt", uploads_dir=uploads_dir,
    )
    assert stored.path == uploads_dir / "outside" / VERSION_ID / "evil.txt"
    assert stored.path.resolve().is_relative_to(uploads_dir.resolve())


@pytest.mark.parametrize("bad_id", ["", "123", "../../x", "not-a-uuid"])
def test_version_id_must_be_a_uuid(tmp_path: Path, bad_id: str) -> None:
    with pytest.raises(ValueError):
        store_original(
            io.BytesIO(b"data"), family_key="Doc", version_id=bad_id,
            file_name="doc.txt", uploads_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_default_folder_comes_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    stored = store_original(
        io.BytesIO(b"data"), family_key="Doc", version_id=VERSION_ID, file_name="doc.txt",
    )
    assert stored.path == tmp_path / "uploads" / "Doc" / VERSION_ID / "doc.txt"


def test_new_version_ids_are_different() -> None:
    ids = {new_version_id() for _ in range(100)}
    assert len(ids) == 100