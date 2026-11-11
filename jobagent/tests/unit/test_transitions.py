"""Status state machine."""

from __future__ import annotations

import pytest

from jobagent.graph.state import STATUS_RANK, JobStatus
from jobagent.store.transitions import (
    LEGAL_TRANSITIONS,
    NEEDS_ATTENTION,
    TERMINAL,
    IllegalTransition,
    assert_legal,
    is_legal,
    outranks,
)


def test_every_status_has_an_entry() -> None:
    assert set(LEGAL_TRANSITIONS) == set(JobStatus)
    assert set(STATUS_RANK) == set(JobStatus)


@pytest.mark.parametrize(
    ("old", "new"),
    [(old, new) for old, allowed in LEGAL_TRANSITIONS.items() for new in allowed],
)
def test_declared_edges_are_accepted(old: JobStatus, new: JobStatus) -> None:
    assert_legal(old, new)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (JobStatus.APPLIED, JobStatus.TAILORED),  # the one that matters most
        (JobStatus.APPLIED, JobStatus.DISCOVERED),
        (JobStatus.APPLIED, JobStatus.SCREENED),
        (JobStatus.CLOSED, JobStatus.APPLIED),
        (JobStatus.DISCOVERED, JobStatus.APPLIED),  # cannot skip screening and tailoring
        (JobStatus.INTERVIEW, JobStatus.APPLIED),
        (JobStatus.SCREENED, JobStatus.APPLIED),
    ],
)
def test_illegal_edges_are_rejected(old: JobStatus, new: JobStatus) -> None:
    assert not is_legal(old, new)
    with pytest.raises(IllegalTransition):
        assert_legal(old, new)


def test_applied_has_no_path_back_to_tailored() -> None:
    """This absence is what makes re-application structurally impossible."""
    assert JobStatus.TAILORED not in LEGAL_TRANSITIONS[JobStatus.APPLIED]


def test_closed_is_terminal() -> None:
    assert LEGAL_TRANSITIONS[JobStatus.CLOSED] == frozenset()
    assert frozenset({JobStatus.CLOSED}) == TERMINAL


@pytest.mark.parametrize("status", list(JobStatus))
def test_self_transition_is_always_legal(status: JobStatus) -> None:
    """Re-running a node must be idempotent, not an error."""
    assert is_legal(status, status)
    assert_legal(status, status)


def test_needs_attention_set() -> None:
    assert {JobStatus.MANUAL_QUEUE, JobStatus.UNCERTAIN, JobStatus.FAILED} == NEEDS_ATTENTION


def test_outranks_is_the_idempotency_predicate() -> None:
    assert outranks(JobStatus.APPLIED, JobStatus.TAILORED)
    assert outranks(JobStatus.TAILORED, JobStatus.TAILORED)
    assert not outranks(JobStatus.SCREENED, JobStatus.TAILORED)


def test_manual_override_paths_exist() -> None:
    """You must be able to disagree with the screener, and to retry a failure."""
    assert JobStatus.SCREENED in LEGAL_TRANSITIONS[JobStatus.SCREENED_OUT]
    assert JobStatus.DISCOVERED in LEGAL_TRANSITIONS[JobStatus.FAILED]
