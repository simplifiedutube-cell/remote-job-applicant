"""Test fixtures.

Split by marker: everything pure runs with the Docker daemon down, and `@pytest.mark.pg`
auto-skips unless `JOBAGENT_TEST_DSN` points at a reachable database.

The test database is the compose-managed `jobagent_test` on the same container -- not
testcontainers, which needs the same daemon, pulls ten transitive dependencies, and costs
a container start per session.

Isolation is an outer transaction that is rolled back, not a truncate: every test sees a
pristine schema at zero cleanup cost. Tests that deliberately provoke a constraint
violation use `begin_nested()` savepoints so the poisoned transaction does not leak.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from jobagent.graph.state import Job, JobStatus
from jobagent.store.dsn import with_search_path
from jobagent.store.models import SCHEMA

TEST_DSN = os.environ.get(
    "JOBAGENT_TEST_DSN", "postgresql://jobagent:jobagent@localhost:5433/jobagent_test"
)

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _reachable(dsn: str) -> bool:
    try:
        engine = create_engine(with_search_path(dsn, SCHEMA, "public"))
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _reachable(TEST_DSN):
        return
    skip = pytest.mark.skip(reason=f"no Postgres at {TEST_DSN} (run `make up && make migrate`)")
    for item in items:
        if "pg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(with_search_path(TEST_DSN, SCHEMA, "public"), future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session inside an outer transaction that is always rolled back.

    `join_transaction_mode="create_savepoint"` matters here: several tests deliberately
    provoke an IntegrityError, which aborts the enclosing transaction. Without the
    savepoint the outer rollback finds itself already deassociated and the fixture warns
    (and, in a longer suite, leaks a poisoned connection between tests).
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def seeded_run(db: Session) -> str:
    from jobagent.store.repo import RunRepo

    run_id = "run_test"
    RunRepo(db).create(run_id=run_id, thread_id=run_id, profile_version="test", dry_run=True)
    return run_id


def make_job(
    *,
    company: str = "Acme Inc",
    title: str = "Senior AI Engineer",
    location: str = "Remote",
    status: JobStatus = JobStatus.DISCOVERED,
    source: str = "remoteok",
    **kwargs: object,
) -> Job:
    defaults: dict[str, object] = {
        "source": source,
        "url": f"https://example.test/{company}/{title}".replace(" ", "-"),
        "company": company,
        "title": title,
        "location": location,
        "status": status,
        "first_seen_at": T0,
        "last_seen_at": T0,
    }
    defaults.update(kwargs)
    return Job(**defaults)
