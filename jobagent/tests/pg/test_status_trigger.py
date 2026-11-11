"""The database trigger, and its agreement with the Python transition table.

`LEGAL_TRANSITIONS` is deliberately duplicated between Python and PL/pgSQL so that a
status change made from psql or a script is rejected too. Duplication without a guard
drifts, so this module walks every edge from Python and asserts the database agrees --
both on what it permits and on what it refuses.
"""

from __future__ import annotations

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.orm import Session

from jobagent.graph.state import JobStatus
from jobagent.store.repo import JobRepo
from jobagent.store.transitions import LEGAL_TRANSITIONS
from jobagent.tests.conftest import make_job

pytestmark = pytest.mark.pg

ALL_EDGES = [(old, new) for old in JobStatus for new in JobStatus if old != new]


def _db_says_legal(db: Session, old: JobStatus, new: JobStatus) -> bool:
    return bool(
        db.execute(
            text(
                "SELECT jobagent.job_status_is_legal("
                "CAST(:old AS jobagent.job_status), CAST(:new AS jobagent.job_status))"
            ),
            {"old": old.value, "new": new.value},
        ).scalar_one()
    )


@pytest.mark.parametrize(("old", "new"), ALL_EDGES, ids=lambda v: getattr(v, "value", v))
def test_sql_table_matches_python_table(db: Session, old: JobStatus, new: JobStatus) -> None:
    """Every one of the 110 possible edges must be decided identically in both places."""
    assert _db_says_legal(db, old, new) is (new in LEGAL_TRANSITIONS[old])


def test_illegal_update_is_rejected_from_raw_sql(db: Session, seeded_run: str) -> None:
    """Layer 2: catches a write that bypassed JobRepo entirely."""
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    for status in (JobStatus.SCREENED, JobStatus.TAILORED, JobStatus.APPLIED):
        repo.update_status(fingerprint, status, run_id=seeded_run)

    with pytest.raises((IntegrityError, InternalError)) as exc:
        db.execute(
            text("UPDATE jobagent.jobs SET status = 'tailored' WHERE fingerprint = :fp"),
            {"fp": fingerprint},
        )
    assert isinstance(exc.value.orig, (pg_errors.CheckViolation, pg_errors.RaiseException))


def test_legal_update_from_raw_sql_is_audited(db: Session, seeded_run: str) -> None:
    """The audit row is written by the trigger, so no caller can omit it."""
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)

    db.execute(
        text("UPDATE jobagent.jobs SET status = 'screened' WHERE fingerprint = :fp"),
        {"fp": fingerprint},
    )
    events = repo.events(fingerprint)
    assert (events[-1].from_status, events[-1].to_status) == (
        JobStatus.DISCOVERED,
        JobStatus.SCREENED,
    )


def test_self_transition_writes_no_audit_row(db: Session, seeded_run: str) -> None:
    """Re-running a node must be idempotent, not append noise to the audit log."""
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    before = len(repo.events(fingerprint))

    db.execute(
        text("UPDATE jobagent.jobs SET status = 'discovered' WHERE fingerprint = :fp"),
        {"fp": fingerprint},
    )
    assert len(repo.events(fingerprint)) == before


def test_insert_logs_the_initial_status(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    events = repo.events(fingerprint)
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status is JobStatus.DISCOVERED
