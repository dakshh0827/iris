"""Tests for app.config (Phase 1, Step 4).

Settings(_env_file=None) builds settings while ignoring your personal .env file,
so these tests give the same result on every machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import PROJECT_ROOT, Settings


def test_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.max_upload_size_mb == 50
    assert settings.max_upload_size_bytes == 50 * 1024 * 1024
    assert settings.data_dir == PROJECT_ROOT / "data"


def test_environment_variable_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MAX_UPLOAD_SIZE_MB", "10")

    assert Settings(_env_file=None).max_upload_size_mb == 10


def test_env_file_is_read_and_environment_variable_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("IRIS_MAX_UPLOAD_SIZE_MB=20\n", encoding="utf-8")

    assert Settings(_env_file=env_file).max_upload_size_mb == 20  # .env beats the default

    monkeypatch.setenv("IRIS_MAX_UPLOAD_SIZE_MB", "10")
    assert Settings(_env_file=env_file).max_upload_size_mb == 10  # environment beats .env


@pytest.mark.parametrize("bad_value", ["abc", "0", "-5", "501"])
def test_invalid_upload_size_is_rejected(bad_value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MAX_UPLOAD_SIZE_MB", bad_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_ENVIRONMENT", "staging")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_lowercase_log_level_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_LOG_LEVEL", "debug")

    assert Settings(_env_file=None).log_level == "DEBUG"


def test_relative_data_dir_is_resolved_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_DATA_DIR", "data")

    settings = Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.regulations_dir == settings.data_dir / "regulations"


def test_supplied_dataset_is_where_settings_expect_it() -> None:
    settings = Settings(_env_file=None)

    assert settings.metadata_csv.is_file()
    assert len(list(settings.regulations_dir.iterdir())) == 10