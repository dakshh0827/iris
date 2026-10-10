"""Things many endpoints need, given to them by FastAPI's Depends() (Phase 9)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.session import session_scope


def get_session() -> Iterator[Session]:
    """One database session per request: committed if the endpoint succeeds, rolled back if it fails.

    Never pass it to a background task: it is closed when the request ends.
    """
    with session_scope() as session:
        yield session
