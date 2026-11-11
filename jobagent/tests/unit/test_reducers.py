"""Reducer contract.

These properties are not stylistic. LangGraph reuses the prior channel value across
checkpoints and hands the same list to parallel `Send` branches, so a reducer that
mutates its input or depends on arrival order produces a run that cannot be resumed
faithfully.
"""

from __future__ import annotations

import copy
from datetime import timedelta

from jobagent.graph.state import (
    CompRange,
    JobStatus,
    RunStats,
    merge_by_fingerprint,
    take_latest_stats,
)
from jobagent.tests.conftest import T0, make_job

T1 = T0 + timedelta(days=1)


def test_none_safe() -> None:
    """`left` is None on the very first write to the channel."""
    assert merge_by_fingerprint(None, None) == []
    assert merge_by_fingerprint([], None) == []
    assert len(merge_by_fingerprint(None, [make_job()])) == 1


def test_purity_left_is_not_mutated() -> None:
    old = make_job(status=JobStatus.DISCOVERED)
    left = [old]
    snapshot = copy.deepcopy(left)

    merge_by_fingerprint(left, [make_job(status=JobStatus.SCREENED, last_seen_at=T1)])

    assert left == snapshot
    assert left[0] is old
    assert old.status is JobStatus.DISCOVERED


def test_commutative() -> None:
    a = make_job(status=JobStatus.DISCOVERED)
    b = make_job(status=JobStatus.SCREENED, last_seen_at=T1, apply_url="https://x.test")

    forward = [j.model_dump() for j in merge_by_fingerprint([a], [b])]
    backward = [j.model_dump() for j in merge_by_fingerprint([b], [a])]
    assert forward == backward


def test_order_stable_across_shuffles() -> None:
    jobs = [make_job(company=f"Co{i}", first_seen_at=T0 + timedelta(minutes=i)) for i in range(6)]
    forward = [j.fingerprint for j in merge_by_fingerprint(jobs, [])]
    reversed_ = [j.fingerprint for j in merge_by_fingerprint(list(reversed(jobs)), [])]
    assert forward == reversed_


def test_status_never_regresses() -> None:
    """A later, thinner sighting must not un-apply a job."""
    applied = make_job(status=JobStatus.APPLIED)
    rediscovered = make_job(status=JobStatus.DISCOVERED, last_seen_at=T1)

    merged = merge_by_fingerprint([applied], [rediscovered])[0]
    assert merged.status is JobStatus.APPLIED
    assert merged.last_seen_at == T1  # freshness still advances


def test_first_seen_takes_min_last_seen_takes_max() -> None:
    early = make_job(first_seen_at=T0, last_seen_at=T0)
    late = make_job(first_seen_at=T1, last_seen_at=T1)
    merged = merge_by_fingerprint([late], [early])[0]
    assert merged.first_seen_at == T0
    assert merged.last_seen_at == T1


def test_enrichment_is_not_lost_to_a_thinner_record() -> None:
    rich = make_job(
        apply_url="https://boards.greenhouse.io/acme/1",
        ats_vendor="greenhouse",
        comp=CompRange(min_amount=100, max_amount=200),
        description="a much longer description",
        tags=("remote",),
    )
    thin = make_job(last_seen_at=T1, tags=("senior",))

    merged = merge_by_fingerprint([rich], [thin])[0]
    assert merged.apply_url == "https://boards.greenhouse.io/acme/1"
    assert merged.ats_vendor == "greenhouse"
    assert merged.comp is not None
    assert merged.description == "a much longer description"
    assert set(merged.tags) == {"remote", "senior"}


def test_distinct_jobs_are_kept_separate() -> None:
    merged = merge_by_fingerprint([make_job(company="Acme")], [make_job(company="Beta")])
    assert len(merged) == 2


def test_take_latest_stats_is_none_safe() -> None:
    # Compared field-by-field rather than by equality: RunStats.started_at defaults to
    # now(), so two freshly-built instances are never equal.
    fresh = take_latest_stats(None, None)
    assert (fresh.applied, fresh.discovered, fresh.dry_run) == (0, 0, True)

    left = RunStats(applied=1)
    assert take_latest_stats(left, None) is left
    right = RunStats(applied=2)
    assert take_latest_stats(left, right) is right
