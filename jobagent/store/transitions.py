"""The job status state machine, expressed as data.

Enforced in three independent places, because a solo project will eventually bypass any
single one:

1. `assert_legal()` here, called by `JobRepo.update_status()` -- the normal path, and the
   one that produces a readable error.
2. A `BEFORE UPDATE` trigger on `jobagent.jobs` re-implementing this table in PL/pgSQL --
   catches writes from `psql`, a one-off script, or a future node that bypasses the repo.
   That trigger also appends the `job_status_events` row, so the audit log cannot be
   forgotten by a caller.
3. The `STATUS_RANK` guard inside `merge_by_fingerprint` -- catches an in-memory
   regression during a parallel superstep, before it ever reaches the database.
"""

from __future__ import annotations

from jobagent.graph.state import STATUS_RANK, JobStatus

__all__ = [
    "LEGAL_TRANSITIONS",
    "NEEDS_ATTENTION",
    "TERMINAL",
    "IllegalTransition",
    "assert_legal",
    "is_legal",
    "outranks",
]


LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DISCOVERED: frozenset(
        {JobStatus.SCREENED, JobStatus.SCREENED_OUT, JobStatus.FAILED, JobStatus.CLOSED}
    ),
    JobStatus.SCREENED: frozenset(
        {
            JobStatus.TAILORED,
            JobStatus.SCREENED_OUT,
            JobStatus.MANUAL_QUEUE,
            JobStatus.FAILED,
            JobStatus.CLOSED,
        }
    ),
    JobStatus.TAILORED: frozenset(
        {
            JobStatus.APPLIED,
            JobStatus.UNCERTAIN,
            JobStatus.MANUAL_QUEUE,
            JobStatus.FAILED,
            JobStatus.CLOSED,
        }
    ),
    JobStatus.MANUAL_QUEUE: frozenset(
        {JobStatus.TAILORED, JobStatus.APPLIED, JobStatus.UNCERTAIN, JobStatus.CLOSED}
    ),
    # UNCERTAIN -> APPLIED is legal, but only from a human confirmation or the M7 inbox
    # watcher seeing an acknowledgement. No apply node may perform it: a node that could
    # promote its own unverified submit to "applied" would defeat guardrail 4 entirely.
    JobStatus.UNCERTAIN: frozenset(
        {JobStatus.APPLIED, JobStatus.ACKED, JobStatus.FAILED, JobStatus.CLOSED}
    ),
    # APPLIED has NO edge back to TAILORED. That absence is what makes re-application
    # structurally impossible rather than merely discouraged.
    JobStatus.APPLIED: frozenset({JobStatus.ACKED, JobStatus.INTERVIEW, JobStatus.CLOSED}),
    JobStatus.ACKED: frozenset({JobStatus.INTERVIEW, JobStatus.CLOSED}),
    JobStatus.INTERVIEW: frozenset({JobStatus.CLOSED}),
    # Manual override: you disagree with the screener and want the job reconsidered.
    JobStatus.SCREENED_OUT: frozenset({JobStatus.SCREENED, JobStatus.CLOSED}),
    # Manual retry after fixing whatever broke.
    JobStatus.FAILED: frozenset({JobStatus.DISCOVERED, JobStatus.CLOSED}),
    JobStatus.CLOSED: frozenset(),
}

TERMINAL: frozenset[JobStatus] = frozenset({JobStatus.CLOSED})

#: Statuses that should surface in the daily digest as requiring a human.
NEEDS_ATTENTION: frozenset[JobStatus] = frozenset(
    {JobStatus.MANUAL_QUEUE, JobStatus.UNCERTAIN, JobStatus.FAILED}
)


class IllegalTransition(ValueError):
    """Raised when a status change would violate `LEGAL_TRANSITIONS`."""


def is_legal(old: JobStatus, new: JobStatus) -> bool:
    """Self-transitions are always legal so that re-running a node stays idempotent."""
    if old == new:
        return True
    return new in LEGAL_TRANSITIONS[old]


def assert_legal(old: JobStatus, new: JobStatus) -> None:
    if not is_legal(old, new):
        allowed = ", ".join(sorted(s.value for s in LEGAL_TRANSITIONS[old])) or "<terminal>"
        raise IllegalTransition(
            f"{old.value} -> {new.value} is not a legal transition; allowed: {allowed}"
        )


def outranks(candidate: JobStatus, floor: JobStatus) -> bool:
    """True when `candidate` represents at least as much progress as `floor`.

    This is the predicate behind `@idempotent(skip_if_status_at_or_above=...)`: it is how
    a resumed run decides that expensive work has already been done.
    """
    return STATUS_RANK[candidate] >= STATUS_RANK[floor]
