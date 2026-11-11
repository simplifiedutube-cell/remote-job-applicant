"""CSV export.

CSV is an artifact, never a source of truth: the whole file is regenerated from Postgres
on every export, so a hand-edit is discarded rather than silently diverging.

`utf-8-sig` because these land in Google Sheets and Excel on macOS, which otherwise
mis-detects the encoding. Header style (Title Case, `(Y/N)` suffixes) matches the
existing `tracker.csv` / `daily-log.csv` conventions in the interview-prep repo.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobagent.graph.state import JobStatus
from jobagent.store import models as m
from jobagent.store.transitions import NEEDS_ATTENTION

__all__ = [
    "APPLICATIONS_HEADER",
    "DAILY_SUMMARY_HEADER",
    "export_applications",
    "export_daily_summary",
    "write_applications_csv",
]

APPLICATIONS_HEADER: tuple[str, ...] = (
    "Date Applied",
    "Company",
    "Title",
    "Location",
    "Source",
    "Status",
    "Fit Score",
    "Apply Route",
    "Job URL",
    "Apply URL",
    "ATS",
    "Comp (min)",
    "Comp (max)",
    "Currency",
    "Resume File",
    "Confirmation",
    "Evidence",
    "Needs Attention (Y/N)",
    "Blocking Reasons",
    "Fingerprint",
    "Run ID",
    "First Seen",
    "Last Updated",
)

DAILY_SUMMARY_HEADER: tuple[str, ...] = (
    "Date",
    "Applications sent",
    "Screened out",
    "Needs attention",
)


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, JobStatus):
        return value.value
    return str(value)


def _rows(session: Session) -> Iterable[tuple[str, ...]]:
    """One row per job, left-joined to its latest application, verdict, and resume."""
    latest_app = (
        select(
            m.Application.fingerprint.label("fp"),
            func.max(m.Application.created_at).label("created_at"),
        )
        .group_by(m.Application.fingerprint)
        .subquery()
    )
    latest_verdict = (
        select(
            m.FitVerdictRow.fingerprint.label("fp"),
            func.max(m.FitVerdictRow.verdict_id).label("verdict_id"),
        )
        .group_by(m.FitVerdictRow.fingerprint)
        .subquery()
    )

    stmt = (
        select(m.Job, m.Application, m.FitVerdictRow, m.Artifact)
        .outerjoin(latest_app, latest_app.c.fp == m.Job.fingerprint)
        .outerjoin(
            m.Application,
            (m.Application.fingerprint == latest_app.c.fp)
            & (m.Application.created_at == latest_app.c.created_at),
        )
        .outerjoin(latest_verdict, latest_verdict.c.fp == m.Job.fingerprint)
        .outerjoin(m.FitVerdictRow, m.FitVerdictRow.verdict_id == latest_verdict.c.verdict_id)
        .outerjoin(
            m.Artifact,
            (m.Artifact.fingerprint == m.Job.fingerprint) & (m.Artifact.kind == "resume_docx"),
        )
        .order_by(m.Job.last_seen_at.desc(), m.Job.fingerprint)
    )

    for job, app, verdict, resume in session.execute(stmt).unique():
        comp = job.comp or {}
        yield (
            _fmt(app.submitted_at if app else None),
            job.company,
            job.title,
            job.location,
            job.source,
            _fmt(job.status),
            _fmt(verdict.score if verdict else None),
            _fmt(app.route if app else None),
            job.url,
            _fmt(job.apply_url),
            _fmt(job.ats_vendor),
            _fmt(comp.get("min_amount")),
            _fmt(comp.get("max_amount")),
            _fmt(comp.get("currency")),
            _fmt(resume.path if resume else None),
            _fmt(app.confirmation_text if app else None),
            _fmt(app.evidence_screenshot if app else None),
            "Y" if job.status in NEEDS_ATTENTION else "N",
            "; ".join(verdict.blocking_reasons) if verdict else "",
            job.fingerprint,
            _fmt(app.run_id if app else job.first_run_id),
            _fmt(job.first_seen_at),
            _fmt(job.last_seen_at),
        )


def write_applications_csv(session: Session, stream: io.TextIOBase) -> int:
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(APPLICATIONS_HEADER)
    count = 0
    for row in _rows(session):
        writer.writerow(row)
        count += 1
    return count


def export_applications(session: Session, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        return write_applications_csv(session, fh)


def export_daily_summary(session: Session, path: Path, *, on: date | None = None) -> int:
    """Counts that paste straight into `daily-log.csv`'s `Applications sent` column."""
    day = on or datetime.now().astimezone().date()

    applied = session.execute(
        select(func.count())
        .select_from(m.Application)
        .where(
            m.Application.result_status == "applied",
            func.date(m.Application.submitted_at) == day,
        )
    ).scalar_one()
    screened_out = session.execute(
        select(func.count()).select_from(m.Job).where(m.Job.status == JobStatus.SCREENED_OUT)
    ).scalar_one()
    attention = session.execute(
        select(func.count()).select_from(m.Job).where(m.Job.status.in_(tuple(NEEDS_ATTENTION)))
    ).scalar_one()

    path.parent.mkdir(parents=True, exist_ok=True)
    rows: Sequence[tuple[str, ...]] = [
        (day.isoformat(), str(applied), str(screened_out), str(attention))
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(DAILY_SUMMARY_HEADER)
        writer.writerows(rows)
    return len(rows)
