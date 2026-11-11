"""LangGraph Postgres checkpointer -- guardrail 7, full resumability.

Pinned to the `langgraph` schema. `PostgresSaver` creates its four tables *unqualified*,
and because the database role and the application schema share the name `jobagent`, the
Postgres default `search_path` would drop them straight into the middle of the
application schema. That is not a style preference: it would also make Alembic
autogenerate try to DROP them.

`PostgresSaver.from_conn_string()` passes its argument to psycopg directly, so it needs a
raw libpq URI -- SQLAlchemy's `postgresql+psycopg://` prefix is rejected as an unknown
connection option.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from jobagent.config import get_config
from jobagent.store.dsn import to_libpq_dsn
from jobagent.store.models import CHECKPOINT_SCHEMA

__all__ = ["checkpointer_scope", "setup_checkpointer"]


@contextmanager
def checkpointer_scope(dsn: str | None = None) -> Iterator[PostgresSaver]:
    resolved = dsn or get_config().db_dsn
    with PostgresSaver.from_conn_string(
        to_libpq_dsn(resolved, CHECKPOINT_SCHEMA, "public")
    ) as saver:
        yield saver


def setup_checkpointer(dsn: str | None = None) -> None:
    """Create the checkpoint tables. Idempotent. Run from `jobagent db init`, never Alembic."""
    with checkpointer_scope(dsn) as saver:
        saver.setup()
