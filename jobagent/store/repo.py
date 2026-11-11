"""Repositories: the normal path in and out of Postgres.

`upsert_job` is the idempotency primitive the whole system leans on. Seeing the same
posting again advances `last_seen_at` and fills in newly-available fields, but never
resets `first_seen_at` and never regresses `status`. That is what makes re-running
discovery cheap and makes a resumed run safe.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement, TextClause

from jobagent.graph.state import (
    ApplicationResult,
    ApplyRoute,
    Artifact,
    ErrorRecord,
    Escalation,
    FitVerdict,
    Job,
    JobStatus,
    RunStats,
)
from jobagent.normalize import norm_company, norm_location, norm_title
from jobagent.store import models as m
from jobagent.store.transitions import assert_legal, outranks

__all__ = ["AppRepo", "ArtifactRepo", "JobRepo", "RunRepo", "new_id"]

log = structlog.get_logger(__name__)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        run_id: str,
        thread_id: str,
        profile_version: str,
        dry_run: bool,
        search_specs: list[dict[str, object]] | None = None,
    ) -> m.Run:
        row = m.Run(
            run_id=run_id,
            thread_id=thread_id,
            profile_version=profile_version,
            dry_run=dry_run,
            status="running",
            search_specs=search_specs or [],
            stats={},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, run_id: str) -> m.Run | None:
        return self.session.get(m.Run, run_id)

    def finish(self, run_id: str, *, status: str, stats: RunStats | None = None) -> None:
        row = self.session.get(m.Run, run_id)
        if row is None:
            raise LookupError(f"unknown run_id {run_id!r}")
        row.status = status
        # The runs_finished_iff_terminal CHECK enforces this pairing; setting one without
        # the other is a constraint violation rather than a silently odd row.
        row.finished_at = _utcnow() if status in {"completed", "failed"} else None
        if stats is not None:
            row.stats = stats.model_dump(mode="json")
        self.session.flush()


class JobRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ writes

    def upsert(self, job: Job, *, run_id: str) -> tuple[str, bool]:
        """Insert or merge a sighting. Returns `(fingerprint, was_inserted)`.

        Idempotent by construction: the fingerprint is the primary key, so a repeat
        sighting is an ON CONFLICT DO UPDATE rather than a duplicate row.
        """
        values = {
            "fingerprint": job.fingerprint,
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "norm_company": norm_company(job.company),
            "norm_title": norm_title(job.title),
            "norm_location": norm_location(job.location),
            "source": job.source,
            "source_job_id": job.source_job_id,
            "url": job.url,
            "apply_url": job.apply_url,
            "ats_vendor": job.ats_vendor,
            "remote_kind": job.remote_kind.value,
            "employment_type": job.employment_type.value,
            "description": job.description,
            "comp": job.comp.model_dump(mode="json") if job.comp else None,
            "timezone_hint": job.timezone_hint,
            "tags": list(job.tags),
            "status": job.status,
            "posted_at": job.posted_at,
            "first_seen_at": job.first_seen_at,
            "last_seen_at": job.last_seen_at,
            "first_run_id": run_id,
        }

        insert_stmt = pg_insert(m.Job).values(**values)
        excluded = insert_stmt.excluded
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[m.Job.fingerprint],
            set_={
                # Freshness advances; provenance does not.
                "last_seen_at": excluded.last_seen_at,
                "url": excluded.url,
                # COALESCE keeps an existing enrichment when the new sighting is thinner.
                "apply_url": _coalesce(excluded.apply_url, m.Job.apply_url),
                "ats_vendor": _coalesce(excluded.ats_vendor, m.Job.ats_vendor),
                "comp": _coalesce(excluded.comp, m.Job.comp),
                "timezone_hint": _coalesce(excluded.timezone_hint, m.Job.timezone_hint),
                "posted_at": _coalesce(excluded.posted_at, m.Job.posted_at),
                "source_job_id": _coalesce(excluded.source_job_id, m.Job.source_job_id),
                "description": _longer(excluded.description, m.Job.description),
                # NOT updated on conflict, on purpose: first_seen_at, first_run_id, and
                # status. Status only ever moves through update_status(), which enforces
                # the state machine; letting an upsert write it would let a re-crawl
                # silently reset an applied job to `discovered`.
            },
        ).returning(m.Job.first_seen_at, m.Job.last_seen_at)

        result = self.session.execute(stmt).one()
        was_inserted = result.first_seen_at == result.last_seen_at == job.first_seen_at
        return job.fingerprint, was_inserted

    def update_status(
        self,
        fingerprint: str,
        new_status: JobStatus,
        *,
        run_id: str | None = None,
        node: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Move a job through the state machine.

        Enforcement layer 1 of 3 -- see `store/transitions.py`. The database trigger is
        layer 2 and writes the audit row, so this method does not write it itself.
        """
        row = self.session.get(m.Job, fingerprint, with_for_update=True)
        if row is None:
            raise LookupError(f"unknown job fingerprint {fingerprint!r}")

        assert_legal(row.status, new_status)
        if row.status == new_status:
            return

        # Consumed by the trigger to populate job_status_events.run_id / node / reason.
        self.session.execute(_set_transition_context(run_id=run_id, node=node, reason=reason))
        row.status = new_status
        self.session.flush()

    def queue_for_human(
        self, fingerprint: str, *, run_id: str, reason: str, detail: dict[str, object] | None = None
    ) -> None:
        """Dead-letter a job. Nothing silently vanishes."""
        stmt = (
            pg_insert(m.ManualQueue)
            .values(
                fingerprint=fingerprint,
                run_id=run_id,
                reason=reason,
                detail=detail or {},
            )
            .on_conflict_do_update(
                index_elements=[m.ManualQueue.fingerprint],
                set_={"reason": reason, "detail": detail or {}, "cleared_at": None},
            )
        )
        self.session.execute(stmt)
        self.update_status(fingerprint, JobStatus.MANUAL_QUEUE, run_id=run_id, reason=reason)

    def record_verdict(self, fingerprint: str, verdict: FitVerdict, *, run_id: str) -> None:
        """Persist a screening verdict. Below-threshold jobs are recorded, never dropped."""
        self.session.add(
            m.FitVerdictRow(
                fingerprint=fingerprint,
                run_id=run_id,
                score=verdict.score,
                rationale=verdict.rationale,
                blocking_reasons=list(verdict.blocking_reasons),
                hard_filters_failed=list(verdict.hard_filters_failed),
                model=verdict.model,
                evaluated_at=verdict.evaluated_at,
            )
        )
        self.session.flush()

    # ------------------------------------------------------------------ reads

    def get(self, fingerprint: str) -> m.Job | None:
        return self.session.get(m.Job, fingerprint)

    def status_of(self, fingerprint: str) -> JobStatus | None:
        return self.session.execute(
            select(m.Job.status).where(m.Job.fingerprint == fingerprint)
        ).scalar_one_or_none()

    def already_at_or_beyond(self, fingerprint: str, floor: JobStatus) -> bool:
        """The pre-expensive-node idempotency check."""
        current = self.status_of(fingerprint)
        return current is not None and outranks(current, floor)

    def by_status(self, *statuses: JobStatus) -> Sequence[m.Job]:
        return self.session.execute(select(m.Job).where(m.Job.status.in_(statuses))).scalars().all()

    def events(self, fingerprint: str) -> Sequence[m.JobStatusEvent]:
        return (
            self.session.execute(
                select(m.JobStatusEvent)
                .where(m.JobStatusEvent.fingerprint == fingerprint)
                .order_by(m.JobStatusEvent.occurred_at, m.JobStatusEvent.event_id)
            )
            .scalars()
            .all()
        )


class AppRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        fingerprint: str,
        run_id: str,
        route: ApplyRoute,
        result: ApplicationResult,
        dry_run: bool,
        attempts: int = 1,
    ) -> str:
        """Persist an application outcome.

        Deliberately performs no ON CONFLICT handling: a second live submit must raise
        `UniqueViolation` from `applications_one_live_submit_uidx`. Swallowing it here
        would convert the strongest guarantee in the system into a silent no-op.
        """
        application_id = new_id("app")
        self.session.add(
            m.Application(
                application_id=application_id,
                fingerprint=fingerprint,
                run_id=run_id,
                route=route,
                result_status=result.status,
                dry_run=dry_run,
                submitted_at=result.submitted_at,
                final_url=result.final_url,
                confirmation_text=result.confirmation_text,
                evidence_screenshot=(
                    str(result.evidence_screenshot) if result.evidence_screenshot else None
                ),
                attempts=attempts,
                error=result.error,
            )
        )
        self.session.flush()
        return application_id

    def live_application(self, fingerprint: str) -> m.Application | None:
        return self.session.execute(
            select(m.Application).where(
                m.Application.fingerprint == fingerprint,
                m.Application.dry_run.is_(False),
                m.Application.result_status.in_(("applied", "uncertain")),
            )
        ).scalar_one_or_none()


class ArtifactRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self, artifact: Artifact, *, fingerprint: str | None = None, run_id: str | None = None
    ) -> str:
        artifact_id = new_id("art")
        self.session.add(
            m.Artifact(
                artifact_id=artifact_id,
                fingerprint=fingerprint,
                run_id=run_id,
                kind=artifact.kind,
                path=str(artifact.path),
                sha256=artifact.sha256,
                bytes=artifact.bytes,
                profile_version=artifact.profile_version,
                meta=dict(artifact.meta),
            )
        )
        self.session.flush()
        return artifact_id

    def record_escalation(self, escalation: Escalation) -> None:
        self.session.add(
            m.Escalation(
                escalation_id=escalation.escalation_id,
                run_id=escalation.run_id,
                fingerprint=escalation.job_fingerprint,
                kind=escalation.kind,
                question=escalation.question,
                node=escalation.node,
                thread_id=escalation.thread_id,
                context=dict(escalation.context),
                answer=escalation.answer,
                created_at=escalation.created_at,
                resolved_at=escalation.resolved_at,
            )
        )
        self.session.flush()

    def record_error(self, error: ErrorRecord) -> None:
        self.session.add(
            m.ErrorRow(
                run_id=error.run_id,
                fingerprint=error.job_fingerprint,
                node=error.node,
                severity=error.severity.value,
                error_type=error.error_type,
                message=error.message,
                traceback=error.traceback,
                retryable=error.retryable,
                occurred_at=error.occurred_at,
            )
        )
        self.session.flush()


# --------------------------------------------------------------------------- helpers


def _coalesce(new: Any, old: Any) -> ColumnElement[Any]:
    """Keep the existing value when the incoming sighting has none.

    Only sound for columns where `None` reaches SQL as a real NULL. Nullable JSONB needs
    `JSONB(none_as_null=True)` or Python `None` arrives as the JSON literal `null`, which
    is not NULL, and this silently discards the previous value.
    """
    return func.coalesce(new, old)


def _longer(new: Any, old: Any) -> ColumnElement[Any]:
    return case((func.length(new) > func.length(old), new), else_=old)


def _set_transition_context(
    *, run_id: str | None, node: str | None, reason: str | None
) -> TextClause:
    """Stash audit metadata in session-local GUCs for the status trigger to read."""
    return text(
        "SELECT set_config('jobagent.run_id', :run_id, true), "
        "       set_config('jobagent.node', :node, true), "
        "       set_config('jobagent.reason', :reason, true)"
    ).bindparams(run_id=run_id or "", node=node or "", reason=reason or "")
