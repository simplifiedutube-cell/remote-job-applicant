"""Schema integrity, checkpointer isolation, and the M1 lane end to end."""

from __future__ import annotations

import io
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from jobagent.graph.state import ApplyRoute, FitVerdict, JobStatus, RunState, RunStats
from jobagent.store.export import APPLICATIONS_HEADER, write_applications_csv
from jobagent.store.models import CHECKPOINT_SCHEMA, SCHEMA
from jobagent.store.repo import AppRepo, JobRepo
from jobagent.tests.conftest import TEST_DSN, make_job

pytestmark = pytest.mark.pg


# ------------------------------------------------------------------ drift


def test_alembic_reports_no_drift() -> None:
    """Keeps "the ORM is the source of truth" honest rather than aspirational.

    Runs against the app database, not the test database, because that is the one
    `make migrate` maintains.
    """
    result = subprocess.run(
        ["uv", "run", "alembic", "check"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "No new upgrade operations detected" in result.stdout + result.stderr


# ------------------------------------------------------------------ isolation


def test_checkpoint_tables_live_in_their_own_schema(engine: Engine) -> None:
    """The role and the app schema are both named `jobagent`, so the default search_path
    would otherwise drop LangGraph's tables into the middle of the application schema --
    where Alembic autogenerate would then try to DROP them."""
    from jobagent.store.checkpointer import setup_checkpointer

    setup_checkpointer(TEST_DSN)

    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT table_schema FROM information_schema.tables "
                    "WHERE table_name LIKE 'checkpoint%'"
                )
            )
            .scalars()
            .all()
        )

    assert rows, "checkpoint tables were not created"
    assert set(rows) == {CHECKPOINT_SCHEMA}
    assert SCHEMA not in set(rows)


def test_pgvector_and_hnsw_indexes_exist(engine: Engine) -> None:
    with engine.connect() as conn:
        indexes = (
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :s"), {"s": SCHEMA}
            )
            .scalars()
            .all()
        )
    assert "profile_evidence_hnsw" in indexes
    assert "job_embeddings_hnsw" in indexes
    assert "applications_one_live_submit_uidx" in indexes


# ------------------------------------------------------------------ export


def test_csv_export_shape(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)
    repo.record_verdict(
        fingerprint,
        FitVerdict(score=82, rationale="strong retrieval + eval match"),
        run_id=seeded_run,
    )

    buffer = io.StringIO()
    rows = write_applications_csv(db, buffer)

    lines = buffer.getvalue().splitlines()
    assert rows == 1
    assert lines[0].split(",") == list(APPLICATIONS_HEADER)
    assert "Acme Inc" in lines[1]
    assert ",82," in lines[1]
    assert lines[1].endswith(f"{fingerprint},{seeded_run},2026-08-01,2026-08-01")


def test_needs_attention_flag(db: Session, seeded_run: str) -> None:
    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    repo.update_status(fingerprint, JobStatus.SCREENED, run_id=seeded_run)

    buffer = io.StringIO()
    write_applications_csv(db, buffer)
    assert ",N," in buffer.getvalue().splitlines()[1]

    repo.queue_for_human(fingerprint, run_id=seeded_run, reason="unsupported claim")
    buffer = io.StringIO()
    write_applications_csv(db, buffer)
    assert ",Y," in buffer.getvalue().splitlines()[1]


def test_export_reflects_an_applied_job(db: Session, seeded_run: str) -> None:
    from jobagent.graph.state import ApplicationResult

    repo = JobRepo(db)
    fingerprint, _ = repo.upsert(make_job(), run_id=seeded_run)
    for status in (JobStatus.SCREENED, JobStatus.TAILORED, JobStatus.APPLIED):
        repo.update_status(fingerprint, status, run_id=seeded_run)
    AppRepo(db).record(
        fingerprint=fingerprint,
        run_id=seeded_run,
        route=ApplyRoute.EXTERNAL_ATS,
        result=ApplicationResult(
            status="applied",
            evidence_screenshot=Path("/tmp/s.png"),
            confirmation_text="Thanks for applying",
            submitted_at=datetime(2026, 8, 2, tzinfo=UTC),
        ),
        dry_run=False,
    )

    buffer = io.StringIO()
    write_applications_csv(db, buffer)
    row = buffer.getvalue().splitlines()[1]
    assert row.startswith("2026-08-02,Acme Inc")
    assert "external_ats" in row
    assert "Thanks for applying" in row


# ------------------------------------------------------------------ the lane


def test_tracker_lane_persists_exports_and_is_rerunnable(tmp_path: Path) -> None:
    """The M1 definition of done, end to end -- including that a second run is a no-op.

    Uses the real engine rather than the rollback fixture, because the lane opens its own
    sessions; the run is namespaced and cleaned up afterwards.
    """
    import os

    from jobagent.config import get_config, reset_config_cache
    from jobagent.graph.subgraphs.tracker import build_tracker_graph
    from jobagent.store.repo import RunRepo
    from jobagent.store.session import dispose_engine, session_scope

    os.environ["JOBAGENT_DB_DSN"] = TEST_DSN
    os.environ["JOBAGENT_EXPORT_DIR"] = str(tmp_path)
    reset_config_cache()
    dispose_engine()

    run_id = "run_lane_test"
    jobs = [make_job(company="Acme Inc"), make_job(company="Beta Labs")]

    try:
        with session_scope() as session:
            RunRepo(session).create(
                run_id=run_id, thread_id=run_id, profile_version="test", dry_run=True
            )

        graph = build_tracker_graph().compile()
        state: RunState = {
            "run_id": run_id,
            "profile_version": "test",
            "search_specs": [],
            "raw_jobs": [],
            "jobs": jobs,
            "escalations": [],
            "errors": [],
            "stats": RunStats(),
        }

        first = graph.invoke(state)
        assert not first.get("errors")
        assert first["stats"].discovered >= 2

        csv_path = Path(get_config().export_dir) / "applications.csv"
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8-sig")
        assert content.startswith(APPLICATIONS_HEADER[0])
        assert "Acme Inc" in content
        assert "Beta Labs" in content
        assert (Path(get_config().export_dir) / "daily-summary.csv").exists()

        # Re-running must not duplicate rows -- fingerprint is the primary key.
        with session_scope() as session:
            RunRepo(session).finish(run_id, status="running")
        second = graph.invoke(state)
        assert not second.get("errors")

        with session_scope() as session:
            total = session.execute(
                text("SELECT count(*) FROM jobagent.jobs WHERE first_run_id = :r"), {"r": run_id}
            ).scalar_one()
        assert total == 2

    finally:
        with session_scope() as session:
            session.execute(
                text("DELETE FROM jobagent.jobs WHERE first_run_id = :r"), {"r": run_id}
            )
            session.execute(text("DELETE FROM jobagent.runs WHERE run_id = :r"), {"r": run_id})
        os.environ.pop("JOBAGENT_EXPORT_DIR", None)
        os.environ.pop("JOBAGENT_DB_DSN", None)
        reset_config_cache()
        dispose_engine()
