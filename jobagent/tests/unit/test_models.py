"""Model-level guardrails.

Guardrail 4 is enforced here as a type error rather than a convention: a node cannot
record a success it cannot prove, because the object will not construct.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobagent.graph.state import (
    ApplicationResult,
    ApplyRoute,
    CompRange,
    FitVerdict,
    Job,
    JobStatus,
)
from jobagent.tests.conftest import make_job

SHOT = Path("/tmp/evidence.png")


# --------------------------------------------------------------- guardrail 4


def test_applied_without_evidence_is_unconstructable() -> None:
    with pytest.raises(ValidationError, match="requires evidence_screenshot"):
        ApplicationResult(status="applied")


def test_applied_with_screenshot_but_no_confirmation_is_rejected() -> None:
    """A screenshot alone does not prove the submit landed."""
    with pytest.raises(ValidationError):
        ApplicationResult(status="applied", evidence_screenshot=SHOT)


@pytest.mark.parametrize(
    "extra",
    [{"confirmation_text": "Thanks for applying!"}, {"final_url": "https://x.test/confirm"}],
)
def test_applied_with_full_evidence_is_accepted(extra: dict[str, str]) -> None:
    result = ApplicationResult(
        status="applied", evidence_screenshot=SHOT, route=ApplyRoute.EXTERNAL_ATS, **extra
    )
    assert result.status == "applied"


def test_uncertain_needs_no_evidence() -> None:
    """The honest fallback when a submit fired but nothing could be captured."""
    assert ApplicationResult(status="uncertain").status == "uncertain"


def test_dry_run_result_needs_no_evidence() -> None:
    assert ApplicationResult(status="skipped_dry_run").status == "skipped_dry_run"


# --------------------------------------------------------------- fit verdict


def test_contradictory_verdict_is_rejected() -> None:
    with pytest.raises(ValidationError, match="blocking_reasons"):
        FitVerdict(score=85, rationale="great fit", blocking_reasons=("no visa sponsorship",))


def test_blocked_with_low_score_is_fine() -> None:
    verdict = FitVerdict(score=10, rationale="blocked", blocking_reasons=("no visa",))
    assert verdict.blocking_reasons == ("no visa",)


@pytest.mark.parametrize("score", [-1, 101])
def test_score_is_bounded(score: int) -> None:
    with pytest.raises(ValidationError):
        FitVerdict(score=score, rationale="x")


# --------------------------------------------------------------- immutability


def test_models_are_frozen() -> None:
    job = make_job()
    with pytest.raises(ValidationError):
        job.status = JobStatus.APPLIED  # type: ignore[misc]


def test_extra_fields_are_forbidden() -> None:
    """An adapter that invents a field fails at the boundary, not silently."""
    with pytest.raises(ValidationError):
        make_job(unexpected_field="oops")


def test_fingerprint_is_computed_not_supplied() -> None:
    job = make_job(company="Acme Inc", title="Senior AI Engineer", location="Remote")
    assert len(job.fingerprint) == 64
    with pytest.raises(ValidationError):
        Job(
            source="x",
            url="u",
            company="Acme",
            title="Eng",
            fingerprint="deadbeef",  # type: ignore[call-arg]
        )


def test_comp_range_ordering_is_validated() -> None:
    with pytest.raises(ValidationError):
        CompRange(min_amount=200, max_amount=100)


def test_currency_must_be_iso_like() -> None:
    with pytest.raises(ValidationError):
        CompRange(currency="rupees")
    assert CompRange(currency="INR").currency == "INR"
