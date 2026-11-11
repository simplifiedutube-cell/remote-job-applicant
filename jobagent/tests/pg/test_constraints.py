"""Database-level guarantees.

Everything here is checked against raw SQL as well as through the repository, because the
whole point of a database constraint is that it holds when the Python is wrong, absent,
or bypassed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jobagent.graph.state import ApplicationResult, ApplyRoute, JobStatus
from jobagent.store.repo import AppRepo, JobRepo
from jobagent.tests.conftest import make_job

pytestmark = pytest.mark.pg

SHOT = Path("/tmp/evidence.png")


def _applied(**kwargs: object) -> ApplicationResult:
    defaults: dict[str, object] = {
        "status": "applied",
        "evidence_screenshot": SHOT,
        "confirmation_text": "Thanks for applying",
        "submitted_at": datetime.now(UTC),
        "route": ApplyRoute.EXTERNAL_ATS,
    }
    defaults.update(kwargs)
    return ApplicationResult(**defaults)


@pytest.fixture
def applied_job(db: Session, seeded_run: str) -> str:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.TAILORED, run_id=seeded_run)
    return fingerprint


# ------------------------------------------------------------------ guardrail 2


def test_second_live_submit_raises(db: Session, seeded_run: str, applied_job: str) -> None:
    """THE constraint. Independent of RetryPolicy, of node-level checks, of resume."""
    repo = AppRepo(db)
    repo.record(
        fingerprint=applied_job,
        run_id=seeded_run,
        route=ApplyRoute.EXTERNAL_ATS,
        result=_applied(),
        dry_run=False,
    )

    with pytest.raises(IntegrityError) as exc:
        repo.record(
            fingerprint=applied_job,
            run_id=seeded_run,
            route=ApplyRoute.EXTERNAL_ATS,
            result=_applied(),
            dry_run=False,
        )
    assert isinstance(exc.value.orig, pg_errors.UniqueViolation)


def test_uncertain_also_blocks_a_later_live_submit(
    db: Session, seeded_run: str, applied_job: str
) -> None:
    """An unconfirmed submit may still have landed, so it must reserve the slot."""
    repo = AppRepo(db)
    repo.record(
        fingerprint=applied_job,
        run_id=seeded_run,
        route=ApplyRoute.BROWSER_AGENT,
        result=ApplicationResult(status="uncertain"),
        dry_run=False,
    )
    with pytest.raises(IntegrityError):
        repo.record(
            fingerprint=applied_job,
            run_id=seeded_run,
            route=ApplyRoute.EXTERNAL_ATS,
            result=_applied(),
            dry_run=False,
        )


def test_unlimited_dry_runs_are_permitted(db: Session, seeded_run: str, applied_job: str) -> None:
    """The `dry_run = false` predicate is what makes iterating on a job free."""
    repo = AppRepo(db)
    for _ in range(5):
        repo.record(
            fingerprint=applied_job,
            run_id=seeded_run,
            route=ApplyRoute.EXTERNAL_ATS,
            result=ApplicationResult(status="skipped_dry_run"),
            dry_run=True,
        )
    assert repo.live_application(applied_job) is None


def test_dry_run_then_one_live_submit_is_allowed(
    db: Session, seeded_run: str, applied_job: str
) -> None:
    repo = AppRepo(db)
    repo.record(
        fingerprint=applied_job,
        run_id=seeded_run,
        route=ApplyRoute.EXTERNAL_ATS,
        result=ApplicationResult(status="skipped_dry_run"),
        dry_run=True,
    )
    repo.record(
        fingerprint=applied_job,
        run_id=seeded_run,
        route=ApplyRoute.EXTERNAL_ATS,
        result=_applied(),
        dry_run=False,
    )
    assert repo.live_application(applied_job) is not None


def test_failed_attempts_do_not_consume_the_slot(
    db: Session, seeded_run: str, applied_job: str
) -> None:
    """A failure is not an application; you must be able to try again."""
    repo = AppRepo(db)
    for _ in range(3):
        repo.record(
            fingerprint=applied_job,
            run_id=seeded_run,
            route=ApplyRoute.BROWSER_AGENT,
            result=ApplicationResult(status="failed", error="captcha"),
            dry_run=False,
        )
    repo.record(
        fingerprint=applied_job,
        run_id=seeded_run,
        route=ApplyRoute.EXTERNAL_ATS,
        result=_applied(),
        dry_run=False,
    )


# ------------------------------------------------------------------ guardrail 4


def test_evidence_check_holds_against_raw_sql(
    db: Session, seeded_run: str, applied_job: str
) -> None:
    """Pydantic refuses this too, but the database must refuse it independently."""
    with pytest.raises(IntegrityError) as exc:
        db.execute(
            text(
                "INSERT INTO jobagent.applications "
                "(application_id, fingerprint, run_id, route, result_status, dry_run) "
                "VALUES ('a1', :fp, :run, 'external_ats', 'applied', false)"
            ),
            {"fp": applied_job, "run": seeded_run},
        )
    assert isinstance(exc.value.orig, pg_errors.CheckViolation)


def test_dry_run_can_never_be_recorded_as_applied(
    db: Session, seeded_run: str, applied_job: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        db.execute(
            text(
                "INSERT INTO jobagent.applications "
                "(application_id, fingerprint, run_id, route, result_status, dry_run, "
                " evidence_screenshot, confirmation_text) "
                "VALUES ('a2', :fp, :run, 'external_ats', 'applied', true, '/tmp/s.png', 'ok')"
            ),
            {"fp": applied_job, "run": seeded_run},
        )
    assert isinstance(exc.value.orig, pg_errors.CheckViolation)


# ------------------------------------------------------------------ jobs table


def test_fingerprint_must_be_sha256_hex(db: Session, seeded_run: str) -> None:
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO jobagent.jobs "
                "(fingerprint, company, title, norm_company, norm_title, norm_location, "
                " source, url, first_run_id) "
                "VALUES ('not-a-hash', 'C', 'T', 'c', 't', 'remote', 's', 'u', :run)"
            ),
            {"run": seeded_run},
        )


def test_same_source_job_id_cannot_be_registered_twice(db: Session, seeded_run: str) -> None:
    """Catches a repost whose title was cosmetically edited past the fingerprint."""
    repo = JobRepo(db)
    repo.upsert(make_job(source_job_id="ext-1"), run_id=seeded_run)
    with pytest.raises(IntegrityError):
        repo.upsert(
            make_job(title="Senior AI Engineer II", source_job_id="ext-1"), run_id=seeded_run
        )


def test_run_finished_pairing_is_enforced(db: Session, seeded_run: str) -> None:
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE jobagent.runs SET status = 'completed' WHERE run_id = :run"),
            {"run": seeded_run},
        )
