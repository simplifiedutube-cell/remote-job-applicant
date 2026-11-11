"""Tracker subgraph -- the one lane that actually executes in M1.

    persist_jobs -> reconcile_status -> export_csv -> update_stats

`persist_jobs` is idempotent by construction: the fingerprint is the primary key, so a
repeated sighting is an upsert that advances `last_seen_at` without disturbing
`first_seen_at`, `first_run_id`, or `status`.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph

from jobagent.config import get_config
from jobagent.graph.state import ErrorRecord, JobStatus, RunState, RunStats, Severity
from jobagent.logging import bind_node
from jobagent.store.export import export_applications, export_daily_summary
from jobagent.store.repo import JobRepo, RunRepo
from jobagent.store.session import session_scope
from jobagent.store.transitions import NEEDS_ATTENTION

__all__ = ["build_tracker_graph", "export_csv", "persist_jobs", "reconcile_status", "update_stats"]


def persist_jobs(state: RunState) -> dict[str, object]:
    """Upsert every job in state. Idempotent; safe to re-run after a crash."""
    run_id = state["run_id"]
    with bind_node("tracker.persist_jobs", run_id=run_id) as log:
        inserted = 0
        errors: list[ErrorRecord] = []

        with session_scope() as session:
            repo = JobRepo(session)
            for job in state.get("jobs", []):
                try:
                    _, was_new = repo.upsert(job, run_id=run_id)
                    inserted += int(was_new)
                except Exception as exc:
                    session.rollback()
                    errors.append(
                        ErrorRecord(
                            run_id=run_id,
                            node="tracker.persist_jobs",
                            job_fingerprint=job.fingerprint,
                            severity=Severity.ERROR,
                            error_type=type(exc).__name__,
                            message=str(exc),
                            traceback=traceback.format_exc(),
                            retryable=True,
                        )
                    )

        log.info(
            "persisted", total=len(state.get("jobs", [])), inserted=inserted, failed=len(errors)
        )
        return {"errors": errors} if errors else {}


def reconcile_status(state: RunState) -> dict[str, object]:
    """Push in-memory status forward through the state machine, one job at a time.

    Illegal transitions are recorded as errors rather than raised: one job in a bad state
    must not abort the run for the other forty.
    """
    run_id = state["run_id"]
    with bind_node("tracker.reconcile_status", run_id=run_id) as log:
        errors: list[ErrorRecord] = []
        moved = 0

        with session_scope() as session:
            repo = JobRepo(session)
            for job in state.get("jobs", []):
                current = repo.status_of(job.fingerprint)
                if current is None or current == job.status:
                    continue
                try:
                    repo.update_status(
                        job.fingerprint,
                        job.status,
                        run_id=run_id,
                        node="tracker.reconcile_status",
                    )
                    moved += 1
                except Exception as exc:
                    session.rollback()
                    errors.append(
                        ErrorRecord(
                            run_id=run_id,
                            node="tracker.reconcile_status",
                            job_fingerprint=job.fingerprint,
                            severity=Severity.WARNING,
                            error_type=type(exc).__name__,
                            message=str(exc),
                            retryable=False,
                        )
                    )

        log.info("reconciled", moved=moved, rejected=len(errors))
        return {"errors": errors} if errors else {}


def export_csv(state: RunState) -> dict[str, object]:
    """Regenerate the CSV artifacts wholesale from Postgres."""
    cfg = get_config()
    with bind_node("tracker.export_csv", run_id=state["run_id"]) as log:
        with session_scope() as session:
            rows = export_applications(session, cfg.export_dir / "applications.csv")
            export_daily_summary(session, cfg.export_dir / "daily-summary.csv")
        log.info("exported", rows=rows, path=str(cfg.export_dir / "applications.csv"))
    return {}


def update_stats(state: RunState) -> dict[str, object]:
    """Recount from the database rather than trusting in-memory increments."""
    run_id = state["run_id"]
    with bind_node("tracker.update_stats", run_id=run_id) as log:
        with session_scope() as session:
            repo = JobRepo(session)
            counts = {
                status: len(repo.by_status(status))
                for status in (
                    JobStatus.DISCOVERED,
                    JobStatus.SCREENED,
                    JobStatus.SCREENED_OUT,
                    JobStatus.TAILORED,
                    JobStatus.APPLIED,
                    JobStatus.UNCERTAIN,
                    JobStatus.MANUAL_QUEUE,
                    JobStatus.FAILED,
                )
            }
            previous = state.get("stats") or RunStats()
            stats = previous.model_copy(
                update={
                    "discovered": counts[JobStatus.DISCOVERED],
                    "screened": counts[JobStatus.SCREENED],
                    "screened_out": counts[JobStatus.SCREENED_OUT],
                    "tailored": counts[JobStatus.TAILORED],
                    "applied": counts[JobStatus.APPLIED],
                    "uncertain": counts[JobStatus.UNCERTAIN],
                    "manual_queue": counts[JobStatus.MANUAL_QUEUE],
                    "failed": counts[JobStatus.FAILED],
                    # Stamped here so the returned stats agree with runs.finished_at;
                    # otherwise the CLI prints finished_at=None for a completed run.
                    "finished_at": datetime.now(UTC),
                }
            )
            RunRepo(session).finish(run_id, status="completed", stats=stats)

        log.info(
            "stats",
            **{k.value: v for k, v in counts.items()},
            needs_attention=sum(counts[s] for s in NEEDS_ATTENTION if s in counts),
        )
        return {"stats": stats}


def build_tracker_graph() -> StateGraph[RunState]:
    builder: StateGraph[RunState] = StateGraph(RunState)
    builder.add_node("persist_jobs", persist_jobs)
    builder.add_node("reconcile_status", reconcile_status)
    builder.add_node("export_csv", export_csv)
    builder.add_node("update_stats", update_stats)

    builder.add_edge(START, "persist_jobs")
    builder.add_edge("persist_jobs", "reconcile_status")
    builder.add_edge("reconcile_status", "export_csv")
    builder.add_edge("export_csv", "update_stats")
    builder.add_edge("update_stats", END)
    return builder
