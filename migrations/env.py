"""Alembic environment.

Two settings here are load-bearing:

* `include_object` returns False for anything in the `langgraph` schema. LangGraph's
  `PostgresSaver.setup()` owns those tables; without this filter, `--autogenerate` sees
  them as unmanaged and emits DROP TABLE for the checkpoint store, which would silently
  destroy resumability.
* `version_table_schema` keeps `alembic_version` inside the `jobagent` schema, so the app
  schema is self-describing and `pg_dump -n jobagent` is restorable on its own.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from jobagent.store.dsn import with_search_path
from jobagent.store.models import CHECKPOINT_SCHEMA, SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_dsn = os.environ.get("JOBAGENT_DB_DSN") or os.environ.get(
    "JOBAGENT_TEST_DSN", "postgresql://jobagent:jobagent@localhost:5433/jobagent"
)
# `search_path=public` -- NOT the application schema -- is required, and the reason is
# non-obvious. The role and the schema are both named `jobagent`, so Postgres' default
# `"$user", public` search_path resolves straight into the application schema. Reflection
# then reports every table as unqualified, autogenerate compares that against the
# schema-qualified models, and every FK and enum shows up as a change forever.
#
# `%` is the configparser interpolation escape -- a percent-encoded password would
# otherwise break set_main_option.
config.set_main_option("sqlalchemy.url", with_search_path(_dsn, "public").replace("%", "%%"))

target_metadata = Base.metadata

_CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Exclude LangGraph's checkpoint tables from autogenerate. See module docstring."""
    if getattr(obj, "schema", None) == CHECKPOINT_SCHEMA:
        return False
    if type_ == "table" and name in _CHECKPOINT_TABLES:
        return False
    # `include_schemas=True` makes Alembic reflect its own bookkeeping table and, seeing
    # no model for it, emit `op.drop_table('alembic_version')` -- which would delete the
    # migration history on first upgrade.
    return not (type_ == "table" and name == "alembic_version")


def render_item(type_, obj, autogen_context) -> str | bool:
    """Teach autogenerate to emit `pgvector.sqlalchemy.Vector(384)` instead of NullType."""
    if type_ == "type" and obj.__class__.__module__.startswith("pgvector"):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector({obj.dim})"
    return False


_COMMON = {
    "target_metadata": target_metadata,
    "include_object": include_object,
    "render_item": render_item,
    "include_schemas": True,
    "version_table_schema": SCHEMA,
    "compare_type": True,
}


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_COMMON,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_COMMON)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
