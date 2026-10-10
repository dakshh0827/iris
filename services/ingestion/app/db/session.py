"""Database connection (Phase 5).

  get_engine()     one shared connection pool, made from IRIS_DATABASE_URL
  session_scope()  a unit of work: commit if everything worked, roll back if not
  check_schema()   confirms the team schema (001-004) and migration 005 are applied

The tables themselves come from the team's migrations, not from this code.
Check from services/ingestion:   python -m app.db.session
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base
from app.errors import ServiceUnavailable


class DatabaseNotConfigured(ServiceUnavailable):
    """IRIS_DATABASE_URL is missing."""


class SchemaMissing(ServiceUnavailable):
    """A table or column ingestion needs is not in the database."""


def make_engine(url: str) -> Engine:
    # pool_pre_ping: test a pooled connection before use, so a restarted Postgres doesn't break us.
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url is None:
        raise DatabaseNotConfigured("IRIS_DATABASE_URL is not set. Add it to .env (see .env.example).")
    return make_engine(url.get_secret_value())


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Use as:  with session_scope() as session: ...   (commits at the end, rolls back on error)."""
    session = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_schema(engine: Engine | Connection | None = None) -> list[str]:
    """Raise SchemaMissing if a table or column we map is missing. Returns the table names checked."""
    inspector = inspect(engine or get_engine())
    tables = set(inspector.get_table_names())
    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            problems.append(f"table {table.name}")
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        problems += [f"column {table.name}.{c.name}" for c in table.columns if c.name not in existing]
    if problems:
        raise SchemaMissing("Missing in the database: " + ", ".join(problems)
                            + ". Apply the team migrations 001-004, then scripts/team-db/005_ingestion_locations.sql.")
    return sorted(table.name for table in Base.metadata.sorted_tables)


if __name__ == "__main__":
    engine = get_engine()
    print("Connected to:", engine.url.render_as_string(hide_password=True))
    print("Schema OK. Tables used by ingestion:", ", ".join(check_schema(engine)))
