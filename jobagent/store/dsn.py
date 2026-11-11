"""DSN handling.

SQLAlchemy resolves a bare `postgresql://` URL to psycopg2, which is not installed and
never will be -- `langgraph-checkpoint-postgres` requires psycopg3. Rather than making
every DSN in `.env`, CI, and the Makefile carry a `+psycopg` suffix that is easy to
forget and produces a confusing `ModuleNotFoundError`, normalize it in one place.

Also sets `search_path` so the app never has to schema-qualify, and so LangGraph's
checkpointer -- which creates its tables unqualified -- lands in `langgraph` rather than
in the middle of the application schema.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["to_libpq_dsn", "to_psycopg_dsn", "with_search_path"]

_PSYCOPG = "postgresql+psycopg"


def to_psycopg_dsn(dsn: str) -> str:
    """Force the psycopg3 driver regardless of how the URL was written."""
    scheme, _, rest = dsn.partition("://")
    if not rest:
        scheme, rest = "postgresql", dsn
    if scheme in {"postgres", "postgresql", "postgresql+psycopg2"}:
        scheme = _PSYCOPG
    return f"{scheme}://{rest}"


def with_search_path(dsn: str, *schemas: str) -> str:
    """Return `dsn` with libpq `options=-csearch_path=<schemas>` applied.

    Always set this explicitly. The database role is named `jobagent` and so is the
    application schema, which means Postgres' default `search_path` of `"$user", public`
    silently resolves to the application schema. Two consequences, both nasty:

    * LangGraph's checkpointer, which creates its tables unqualified, would land them in
      the middle of the application schema rather than in `langgraph`.
    * Alembic reflection would report every application table as living in the default
      schema, so autogenerate compares them against schema-qualified models and emits an
      endless spurious diff. Migrations therefore run with `search_path=public`.
    """
    parts = urlsplit(to_psycopg_dsn(dsn))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={','.join(schemas)}"
    return urlunsplit(parts._replace(query=urlencode(query)))


def to_libpq_dsn(dsn: str, *schemas: str) -> str:
    """Return a raw libpq conninfo URI, optionally pinned to `schemas`.

    `PostgresSaver.from_conn_string()` hands its argument straight to psycopg, which
    rejects SQLAlchemy's `postgresql+psycopg://` dialect prefix as an unknown connection
    option. The checkpointer therefore needs this form, not `with_search_path()`.
    """
    parts = urlsplit(to_psycopg_dsn(dsn))._replace(scheme="postgresql")
    if schemas:
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["options"] = f"-csearch_path={','.join(schemas)}"
        parts = parts._replace(query=urlencode(query))
    return urlunsplit(parts)
