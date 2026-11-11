"""Repository behaviour against a real Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from jobagent.graph.state import CompRange, JobStatus
from jobagent.store.repo import JobRepo
from jobagent.store.transitions import IllegalTransition
from jobagent.tests.conftest import T0, make_job

pytestmark = pytest.mark.pg

T1 = T0 + timedelta(days=1)


def test_roundtrip(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    job = make_job()
    fingerprint, was_new = repo.upsert(job, run_id=seeded_run)

    assert was_new is True
    row = repo.get(fingerprint)
    assert row is not None
    assert row.company == "Acme Inc"
    assert row.norm_company == "acme"  # normalized form persisted for auditability
    assert row.status is JobStatus.DISCOVERED


def test_upsert_is_idempotent(db: Session, seeded_run: str) -> None:
    """Seeing the same posting again must not create a second row."""
    repo = JobRepo(db)
    repo.upsert(make_job(), run_id=seeded_run)
    fingerprint, was_new = repo.upsert(make_job(last_seen_at=T1), run_id=seeded_run)

    assert was_new is False
    row = repo.get(fingerprint)
    assert row is not None
    assert row.first_seen_at == T0  # provenance does not move
    assert row.last_seen_at == T1  # freshness does


def test_upsert_does_not_lose_enrichment(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    repo.upsert(
        make_job(
            apply_url="https://boards.greenhouse.io/acme/1",
            ats_vendor="greenhouse",
            comp=CompRange(min_amount=100, max_amount=200),
            description="a long description",
        ),
        run_id=seeded_run,
    )
    # A thinner later sighting from a different portal.
    fingerprint, _ = repo.upsert(make_job(last_seen_at=T1, description="short"), run_id=seeded_run)

    row = repo.get(fingerprint)
    assert row is not None
    assert row.apply_url == "https://boards.greenhouse.io/acme/1"
    assert row.ats_vendor == "greenhouse"
    assert row.comp is not None
    assert row.description == "a long description"


def test_upsert_never_writes_status(db: Session, seeded_run: str) -> None:
    """A re-crawl must not be able to reset an applied job to `discovered`.

    This is why `status` is excluded from the ON CONFLICT SET clause: status moves only
    through update_status(), which enforces the state machine.
    """
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.TAILORED, run_id=seeded_run)

    repo.upsert(make_job(status=JobStatus.DISCOVERED, last_seen_at=T1), run_id=seeded_run)

    assert repo.status_of(fingerprint) is JobStatus.TAILORED


def test_update_status_rejects_illegal_transition(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    with pytest.raises(IllegalTransition):
        repo.update_status(fingerprint, JobStatus.APPLIED, run_id=seeded_run)

    assert repo.status_of(fingerprint) is JobStatus.DISCOVERED


def test_status_events_are_recorded_automatically(db: Session, seeded_run: str) -> None:
    """The trigger writes the audit row, so a caller cannot forget it."""
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(
        fingerprint, JobStatus.SCREENED_OUT, run_id=seeded_run, reason="comp below floor"
    )

    events = repo.events(fingerprint)
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, JobStatus.DISCOVERED),
        (JobStatus.DISCOVERED, JobStatus.SCREENED_OUT),
    ]
    assert events[-1].reason == "comp below floor"


def test_idempotency_predicate(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    assert repo.already_at_or_beyond(fingerprint, JobStatus.TAILORED) is False

    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.TAILORED, run_id=seeded_run)
    assert repo.already_at_or_beyond(fingerprint, JobStatus.TAILORED) is True


def test_queue_for_human_moves_status_and_records_reason(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)

    repo.queue_for_human(
        fingerprint, run_id=seeded_run, reason="unsupported claim: PyTorch", detail={"claim": "x"}
    )

    assert repo.status_of(fingerprint) is JobStatus.MANUAL_QUEUE
    assert repo.events(fingerprint)[-1].reason == "unsupported claim: PyTorch"
