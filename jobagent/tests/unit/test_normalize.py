"""Normalization and fingerprint stability.

The golden hash below is deliberately pinned. Changing any normalization rule
re-fingerprints every row in the database, so that must be a decision someone makes on
purpose, not a side effect of tidying a regex.
"""

from __future__ import annotations

import pytest

from jobagent.normalize import job_fingerprint, norm_company, norm_location, norm_title

# --------------------------------------------------------------------------- company


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Inc.", "acme"),
        ("Acme, Inc", "acme"),
        ("ACME  INC.", "acme"),
        ("Acme Technologies Pvt. Ltd.", "acme technologies"),
        ("Acme Pvt Ltd", "acme"),
        ("The Acme Corporation", "acme"),
        ("Zürich Insurance AG", "zurich insurance"),
        ("Foo & Bar LLC", "foo and bar"),
        ("Acme | Careers", "acme"),
    ],
)
def test_norm_company(raw: str, expected: str) -> None:
    assert norm_company(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["KNS Technologies", "Acme Labs", "Foo Systems", "Bar Solutions"],
)
def test_distinguishing_suffixes_are_kept(raw: str) -> None:
    """'KNS Technologies' must never collapse to 'kns' -- that would merge real companies."""
    assert norm_company(raw) == raw.casefold()


def test_group_and_holdings_are_not_stripped() -> None:
    """Deliberately conservative: a false merge hides a job permanently."""
    assert norm_company("Access Group") != norm_company("Access")


# --------------------------------------------------------------------------- title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior AI Engineer (m/f/d) [Remote] (#4821)", "senior ai engineer"),
        ("Senior AI Engineer", "senior ai engineer"),
        ("AI Engineer - Remote", "ai engineer"),
        ("AI Engineer, Remote", "ai engineer"),
        ("ML Engineer [REQ 11823]", "ml engineer"),
        ("Sr. Engineer - 118234", "sr engineer"),
        ("Backend Engineer (US)", "backend engineer"),
    ],
)
def test_norm_title(raw: str, expected: str) -> None:
    assert norm_title(raw) == expected


def test_seniority_is_never_collapsed() -> None:
    """Merging Senior into Junior would hide one of two genuinely different reqs."""
    assert norm_title("Senior AI Engineer") != norm_title("Junior AI Engineer")
    assert norm_title("Staff Engineer") != norm_title("Principal Engineer")


def test_meaningful_parentheticals_survive() -> None:
    """'(Contract)' and a team name change what the job is; they are not noise."""
    assert "contract" in norm_title("Staff Engineer (Contract)")
    assert "payments" in norm_title("Backend Engineer (Payments Team)")


# --------------------------------------------------------------------------- location


@pytest.mark.parametrize(
    "raw",
    ["Remote", "Anywhere", "Work From Home", "WFH", "100% Remote", "Worldwide", "Remote-first"],
)
def test_remote_synonyms_collapse(raw: str) -> None:
    assert norm_location(raw) == "remote"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Remote (US only)", "remote:us"),
        ("US Remote", "remote:us"),
        ("Remote - India", "remote:in"),
        ("Remote, EMEA", "remote:emea"),
        ("Remote (North America)", "remote:namer"),
        ("Hyderabad, India", "hyderabad,india"),
        ("Bangalore", "bangalore"),
        ("", "unspecified"),
    ],
)
def test_norm_location(raw: str, expected: str) -> None:
    assert norm_location(raw) == expected


def test_regions_do_not_merge() -> None:
    """The important one: collapsing these hides a job the candidate IS eligible for."""
    assert norm_location("Remote (US only)") != norm_location("Remote - India")
    assert norm_location("Remote (US)") != norm_location("Remote, EMEA")


# --------------------------------------------------------------------------- fingerprint

GOLDEN_INPUT = (
    "Acme Technologies Pvt. Ltd.",
    "Senior AI Engineer (m/f/d) [Remote] (#4821)",
    "Remote (US only)",
)
GOLDEN_HASH = "319ef14d44b9e86b66b4595be181fc2c730f3be19fbd873b1634bdf3e5661043"


def test_fingerprint_golden_hash() -> None:
    """Pinned on purpose. If this fails, you changed normalization -- confirm you meant to.

    A silent change here re-fingerprints the entire jobs table, orphaning every
    application, artifact and status event already recorded against the old values.
    """
    assert job_fingerprint(*GOLDEN_INPUT) == GOLDEN_HASH


def test_fingerprint_is_stable_across_cosmetic_variation() -> None:
    assert job_fingerprint("Acme Inc", "Senior AI Engineer", "Remote") == job_fingerprint(
        "ACME, Inc.", "Senior AI Engineer (Remote)", "Anywhere"
    )


def test_fingerprint_separates_genuinely_different_jobs() -> None:
    base = job_fingerprint("Acme", "Senior AI Engineer", "Remote")
    assert base != job_fingerprint("Acme", "Junior AI Engineer", "Remote")
    assert base != job_fingerprint("Beta", "Senior AI Engineer", "Remote")
    assert base != job_fingerprint("Acme", "Senior AI Engineer", "Remote (India)")


def test_fingerprint_is_lowercase_hex_of_expected_length() -> None:
    fp = job_fingerprint("A", "B", "C")
    assert len(fp) == 64
    assert fp == fp.lower()
    assert set(fp) <= set("0123456789abcdef")
