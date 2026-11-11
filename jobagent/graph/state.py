"""Domain models, graph state channels, and reducers.

Two things in this module are guardrails rather than documentation:

* `ApplicationResult` cannot be constructed with `status="applied"` unless evidence is
  attached. Guardrail 4 is therefore a type error, not a code-review item -- no node can
  record a success it cannot prove. The same rule is restated as a CHECK constraint in
  `store/models.py`, so it also holds against raw SQL.
* `merge_by_fingerprint` never lets a job's status regress. A late, thin sighting of an
  already-applied job cannot un-apply it.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from jobagent.normalize import job_fingerprint

__all__ = [
    "STATUS_RANK",
    "ApplicationResult",
    "ApplyRoute",
    "Artifact",
    "CompRange",
    "EmploymentType",
    "ErrorRecord",
    "Escalation",
    "FitVerdict",
    "Job",
    "JobOutput",
    "JobState",
    "JobStatus",
    "RawJob",
    "RemoteKind",
    "RunState",
    "RunStats",
    "SearchSpec",
    "Severity",
    "merge_by_fingerprint",
    "take_latest_stats",
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Frozen(BaseModel):
    """Base for every value object.

    Immutable, so a model shared across parallel `Send` branches cannot be mutated by one
    branch and observed half-changed by another. `extra="forbid"` turns an adapter that
    invents a field into an error at the boundary rather than a silently ignored value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- enums


class JobStatus(StrEnum):
    """The spec's happy path, plus the four states every real run actually produces."""

    DISCOVERED = "discovered"
    SCREENED = "screened"
    TAILORED = "tailored"
    APPLIED = "applied"
    ACKED = "acked"
    INTERVIEW = "interview"
    CLOSED = "closed"

    SCREENED_OUT = "screened_out"  # filtered out -- always with a recorded reason
    MANUAL_QUEUE = "manual_queue"  # needs a human: unsupported claim, captcha, low conf.
    UNCERTAIN = "uncertain"  # submit fired, no confirmation evidence captured
    FAILED = "failed"  # unrecoverable adapter/render error


# Monotonic progress ranking. Used by the reducer to reject in-memory status regressions
# and by `@idempotent` to decide whether expensive work has already been done.
STATUS_RANK: dict[JobStatus, int] = {
    JobStatus.DISCOVERED: 0,
    JobStatus.SCREENED: 10,
    JobStatus.SCREENED_OUT: 15,
    JobStatus.TAILORED: 20,
    JobStatus.MANUAL_QUEUE: 25,
    JobStatus.UNCERTAIN: 30,
    JobStatus.APPLIED: 40,
    JobStatus.ACKED: 50,
    JobStatus.INTERVIEW: 60,
    JobStatus.FAILED: 70,
    JobStatus.CLOSED: 80,
}


class ApplyRoute(StrEnum):
    PORTAL_FORM = "portal_form"
    LINKEDIN_EASY = "linkedin_easy"
    EXTERNAL_ATS = "external_ats"
    BROWSER_AGENT = "browser_agent"
    MANUAL = "manual"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class RemoteKind(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


# --------------------------------------------------------------------------- search


class CompRange(Frozen):
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    period: Literal["year", "month", "day", "hour"] = "year"

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("comp min_amount exceeds max_amount")
        return self


class SearchSpec(Frozen):
    """One (portal, query) pair. Discovery fans out one `Send` per spec."""

    spec_id: str = Field(min_length=1)  # stable slug, e.g. "remoteok:ai-engineer"
    portal: str = Field(min_length=1)  # must resolve in the portal registry
    query: str = Field(min_length=1)
    location: str | None = None
    remote_only: bool = True
    employment_types: tuple[EmploymentType, ...] = (
        EmploymentType.FULL_TIME,
        EmploymentType.CONTRACT,
        EmploymentType.FREELANCE,
    )
    posted_within_days: int = Field(default=14, ge=1, le=90)
    max_results: int = Field(default=50, ge=1, le=500)
    extra: dict[str, str] = Field(default_factory=dict)  # portal-specific knobs


# --------------------------------------------------------------------------- jobs


class RawJob(Frozen):
    """Verbatim adapter output, never edited.

    Kept so a normalizer change can be replayed against real historical payloads instead
    of re-crawling -- which portal rate limits make expensive and ToS makes unwise.
    """

    source: str = Field(min_length=1)
    source_job_id: str | None = None
    url: str = Field(min_length=1)
    payload: dict[str, object]
    spec_id: str | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)


class Job(Frozen):
    """A normalized posting. `fingerprint` is derived and can never be supplied."""

    source: str = Field(min_length=1)
    source_job_id: str | None = None
    url: str = Field(min_length=1)
    apply_url: str | None = None

    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = "Remote"
    remote_kind: RemoteKind = RemoteKind.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN

    description: str = ""
    comp: CompRange | None = None
    timezone_hint: str | None = None  # "EST overlap 4h" -- parsed by M3's hard filters
    posted_at: datetime | None = None

    status: JobStatus = JobStatus.DISCOVERED
    ats_vendor: str | None = None  # set by the M5 router: greenhouse|lever|ashby|workday
    tags: tuple[str, ...] = ()

    first_seen_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fingerprint(self) -> str:
        return job_fingerprint(self.company, self.title, self.location)


# --------------------------------------------------------------------------- per-job


class FitVerdict(Frozen):
    score: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)
    blocking_reasons: tuple[str, ...] = ()
    hard_filters_failed: tuple[str, ...] = ()  # deterministic filters, evaluated pre-LLM
    model: str | None = None  # None => decided by hard filters alone, no LLM spend
    evaluated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _blocked_implies_low_score(self) -> Self:
        """Stop M3's LLM from returning an internally contradictory verdict."""
        if self.blocking_reasons and self.score >= 70:
            raise ValueError(
                f"blocking_reasons={self.blocking_reasons} is incompatible with "
                f"score={self.score}; a blocked job cannot score as a good fit"
            )
        return self


class Artifact(Frozen):
    kind: Literal["resume_docx", "resume_pdf", "cover_letter", "screenshot", "page_html"]
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    profile_version: str | None = None  # which master_profile.yaml produced it
    meta: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ApplicationResult(Frozen):
    """Guardrail 4, enforced at the type layer.

    `status="applied"` is unconstructable without a screenshot plus either confirmation
    text or a final URL. A node that submitted but captured nothing has exactly one
    honest option available to it: `status="uncertain"`.
    """

    status: Literal["applied", "uncertain", "failed", "skipped_dry_run"]
    route: ApplyRoute | None = None
    submitted_at: datetime | None = None
    final_url: str | None = None
    confirmation_text: str | None = None
    evidence_screenshot: Path | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _applied_requires_evidence(self) -> Self:
        if self.status == "applied" and not (
            self.evidence_screenshot and (self.confirmation_text or self.final_url)
        ):
            raise ValueError(
                "status='applied' requires evidence_screenshot AND "
                "(confirmation_text or final_url). Use status='uncertain' instead -- "
                "an unverified submit must never be recorded as a success."
            )
        return self


class Escalation(Frozen):
    escalation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    kind: Literal[
        "low_confidence_field",
        "unsupported_claim",
        "captcha",
        "login_required",
        "ambiguous_route",
        "rate_limited",
        "review_before_submit",
        "interview_invite",
        "assessment_link",
    ]
    question: str = Field(min_length=1)
    node: str = Field(min_length=1)
    job_fingerprint: str | None = None
    thread_id: str | None = None  # to resume the interrupt()ed graph
    context: dict[str, str] = Field(default_factory=dict)
    answer: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None


class ErrorRecord(Frozen):
    run_id: str = Field(min_length=1)
    node: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    message: str
    job_fingerprint: str | None = None
    severity: Severity = Severity.ERROR
    traceback: str | None = None
    retryable: bool = False
    occurred_at: datetime = Field(default_factory=_utcnow)


class RunStats(Frozen):
    discovered: int = 0
    deduped: int = 0
    screened: int = 0
    screened_out: int = 0
    tailored: int = 0
    applied: int = 0
    uncertain: int = 0
    manual_queue: int = 0
    failed: int = 0
    dry_run: bool = True
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None


# --------------------------------------------------------------------------- reducers


def _merge_one(old: Job, new: Job) -> Job:
    """Field-level merge of two sightings of the same fingerprint. Pure."""
    keep_old_status = STATUS_RANK[old.status] > STATUS_RANK[new.status]
    fresher = new if new.last_seen_at >= old.last_seen_at else old
    stale = old if fresher is new else new

    return fresher.model_copy(
        update={
            # Progress is monotonic: a later thin sighting cannot un-apply a job.
            "status": old.status if keep_old_status else new.status,
            "first_seen_at": min(old.first_seen_at, new.first_seen_at),
            "last_seen_at": max(old.last_seen_at, new.last_seen_at),
            # Never lose an enrichment by seeing a thinner record afterwards.
            "apply_url": fresher.apply_url or stale.apply_url,
            "ats_vendor": fresher.ats_vendor or stale.ats_vendor,
            "comp": fresher.comp or stale.comp,
            "timezone_hint": fresher.timezone_hint or stale.timezone_hint,
            "posted_at": fresher.posted_at or stale.posted_at,
            "source_job_id": fresher.source_job_id or stale.source_job_id,
            "description": max(old.description, new.description, key=len),
            "tags": tuple(dict.fromkeys((*old.tags, *new.tags))),
        }
    )


def merge_by_fingerprint(left: list[Job] | None, right: list[Job] | None) -> list[Job]:
    """LangGraph reducer for `RunState["jobs"]`.

    Three contract properties, each of which has bitten people:

    * **None-safe.** `left` is `None` on the very first write to the channel.
    * **Pure.** Never mutates its inputs. LangGraph reuses the prior value object across
      checkpoints, and parallel `Send` branches may hold the same list.
    * **Order-stable.** The final `sorted()` is load-bearing, not cosmetic. Parallel
      branches complete in nondeterministic order; without a content-derived sort,
      identical input yields different list orders, which changes the serialized
      checkpoint and breaks the "resume picks up exactly where it left off" guarantee.
    """
    if not left and not right:
        return []

    merged: dict[str, Job] = {}
    for job in (*(left or ()), *(right or ())):
        fingerprint = job.fingerprint
        existing = merged.get(fingerprint)
        merged[fingerprint] = _merge_one(existing, job) if existing is not None else job

    return sorted(merged.values(), key=lambda j: (j.first_seen_at, j.fingerprint))


def take_latest_stats(left: RunStats | None, right: RunStats | None) -> RunStats:
    """Last-write-wins with a None-safe floor.

    `stats` needs an explicit reducer even though it is a scalar: a bare unannotated
    channel raises `InvalidUpdateError` the moment two parallel branches write it in the
    same superstep, which is exactly what fanning out over jobs does.
    """
    return right or left or RunStats()


# --------------------------------------------------------------------------- state


class RunState(TypedDict):
    """Root supervisor state."""

    run_id: str
    profile_version: str
    search_specs: list[SearchSpec]
    raw_jobs: Annotated[list[RawJob], operator.add]
    jobs: Annotated[list[Job], merge_by_fingerprint]
    escalations: Annotated[list[Escalation], operator.add]
    errors: Annotated[list[ErrorRecord], operator.add]
    stats: Annotated[RunStats, take_latest_stats]


class JobState(TypedDict):
    """Per-job subgraph state, fanned out with `Send("job_pipeline", {...})`."""

    run_id: str
    job: Job
    fit: FitVerdict | None
    resume: Artifact | None
    cover_letter: str | None
    apply_route: ApplyRoute | None
    attempts: int
    needs_human: bool
    result: ApplicationResult | None


class JobOutput(TypedDict, total=False):
    """Output schema of the per-job subgraph.

    Every key here is a *parent* `RunState` key. That is the wiring trick that lets a
    `Send`-fanned subgraph write back through the parent's reducers -- `jobs` merges by
    fingerprint, `errors` and `escalations` append -- with no manual collector node.
    """

    jobs: list[Job]
    escalations: list[Escalation]
    errors: list[ErrorRecord]
