"""Engine and session factory.

`search_path` is pinned to `jobagent,public` explicitly rather than relying on Postgres'
default. See `jobagent.store.dsn` for why the default is actively dangerous here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobagent.config import get_config
from jobagent.store.dsn import with_search_path
from jobagent.store.models import SCHEMA

__all__ = ["dispose_engine", "get_engine", "session_scope"]


_ENGINES: dict[str, Engine] = {}


def get_engine(dsn: str | None = None) -> Engine:
    resolved = with_search_path(dsn or get_config().db_dsn, SCHEMA, "public")
    engine = _ENGINES.get(resolved)
    if engine is None:
        engine = create_engine(
            resolved,
            pool_pre_ping=True,  # browser lanes idle for minutes; stale connections are real
            future=True,
        )
        _ENGINES[resolved] = engine
    return engine


def dispose_engine() -> None:
    """Dispose every pooled engine. Used by tests and by CLI shutdown."""
    while _ENGINES:
        _, engine = _ENGINES.popitem()
        engine.dispose()


@contextmanager
def session_scope(dsn: str | None = None) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    factory = sessionmaker(bind=get_engine(dsn), expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
