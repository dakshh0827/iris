"""Application settings for the ingestion service.

Every value the service needs from its environment (folders, limits, log level,
and later the database and search addresses) is defined here, in one place.

Values are read in this order (first match wins):
  1. real environment variables, e.g.  IRIS_MAX_UPLOAD_SIZE_MB=10
  2. the .env file in the project root
  3. the defaults written below

Try it from services/ingestion:   python -m app.config
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# This file is <root>/services/ingestion/app/config.py, so the project root is
# three folders above the "app" folder. Computing it (instead of writing
# "D:\..." here) keeps the code free of machine-specific paths.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All configurable values, with types, defaults and limits."""

    model_config = SettingsConfigDict(
        env_prefix="IRIS_",                    # IRIS_DATA_DIR sets data_dir, and so on
        env_file=PROJECT_ROOT / ".env",        # absolute path: works from any folder
        env_file_encoding="utf-8",
        extra="ignore",                        # other services' variables in .env are not an error
    )

    app_name: str = "reg-iris-ingestion"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Root of all data. Locally <project root>/data; in Docker (Phase 12) we set IRIS_DATA_DIR=/data.
    data_dir: Path = PROJECT_ROOT / "data"

    # Uploads bigger than this are rejected in Phase 2. EMIR_2021_v2.pdf (3.8 MB) is our largest file.
    max_upload_size_mb: int = Field(default=50, gt=0, le=500)

    # PostgreSQL (Phase 5). No default: the password must come from .env, never from code.
    # SecretStr hides the value when settings are printed or logged.
    # Example: postgresql+psycopg://iris:<password>@localhost:5432/reg_iris
    database_url: SecretStr | None = None
    # A separate, throw-away database for the tests. Tests that need it are skipped when it isn't set.
    test_database_url: SecretStr | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def upper_case_log_level(cls, value: object) -> object:
        """Accept 'info' as well as 'INFO' from the environment."""
        return value.upper() if isinstance(value, str) else value

    @field_validator("data_dir")
    @classmethod
    def make_data_dir_absolute(cls, value: Path) -> Path:
        """Turn a relative IRIS_DATA_DIR into an absolute path under the project root.

        Without this, 'data' would mean a different folder depending on where you
        started Python from.
        """
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    # Derived paths. They are properties (not settings) so they always follow data_dir.
    @property
    def regulations_dir(self) -> Path:
        """Folder holding the supplied regulation documents (seed source)."""
        return self.data_dir / "regulations"

    @property
    def metadata_csv(self) -> Path:
        """The supplied doc_metadata.csv file."""
        return self.data_dir / "metadata" / "doc_metadata.csv"

    @property
    def uploads_dir(self) -> Path:
        """Where Phase 2 stores the original uploaded files."""
        return self.data_dir / "uploads"

    @property
    def max_upload_size_bytes(self) -> int:
        """The upload limit in bytes, which is what file sizes are measured in."""
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the settings, created once and then reused.

    Reading .env on every call would be wasteful, and one shared object means
    every module sees the same values. Tests can call get_settings.cache_clear()
    to force a fresh read.
    """
    return Settings()


if __name__ == "__main__":
    settings = get_settings()
    print(f"Project root:        {PROJECT_ROOT}")
    print(f".env file found:     {(PROJECT_ROOT / '.env').is_file()}")
    print()
    for name, value in settings.model_dump().items():
        print(f"{name:<20} {value}")
    print()
    print(f"{'regulations_dir':<20} {settings.regulations_dir}  (exists: {settings.regulations_dir.is_dir()})")
    print(f"{'metadata_csv':<20} {settings.metadata_csv}  (exists: {settings.metadata_csv.is_file()})")
    print(f"{'uploads_dir':<20} {settings.uploads_dir}  (created in Phase 2)")
    print(f"{'max_upload_bytes':<20} {settings.max_upload_size_bytes:,}")
