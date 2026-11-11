"""Provenance of `master_profile.yaml`.

This file is the definition of "not fabricated" for M4's `verify_claims`, so it gets a
test asserting where its contents did *not* come from. The `answers/*.md` files in the
interview-prep repo carry an explicit "ILLUSTRATIVE NUMBERS, NOT YOUR DATA" banner; a
plausible-sounding invented metric leaking from there into ground truth is exactly the
failure this whole subsystem exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jobagent.profile.schema import MasterProfile, load_profile

#: The interview-prep repo, if it happens to be checked out beside this one.
ILLUSTRATIVE_SOURCE = Path.home() / "Documents" / "Interview prep" / "answers"


@pytest.fixture(scope="module")
def profile() -> MasterProfile:
    return load_profile()


def test_profile_parses(profile: MasterProfile) -> None:
    assert profile.version
    assert profile.employment
    assert profile.bullets


def test_every_bullet_has_a_stable_evidence_id(profile: MasterProfile) -> None:
    """verify_claims cites these; an unstable id breaks traceability."""
    ids = [b.evidence_id for b in profile.bullets]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", i) for i in ids), ids


def test_experience_bullets_name_a_known_employer(profile: MasterProfile) -> None:
    known = {e.employer for e in profile.employment}
    for bullet in profile.bullets:
        if bullet.section == "experience":
            assert bullet.employer in known


def test_every_quantified_claim_is_structured(profile: MasterProfile) -> None:
    """A number in prose cannot be diffed; a Metric can."""
    for bullet in profile.bullets:
        numbers = re.findall(r"\b\d[\d,]*%?\b", bullet.text)
        if numbers:
            assert bullet.metrics, f"{bullet.evidence_id} states {numbers} but declares no metrics"


def test_ambiguous_identity_fields_are_flagged_not_guessed(profile: MasterProfile) -> None:
    """The resume contradicts itself on the GitHub handle and carries two emails.

    Emitting either without confirmation puts a wrong link on a real application, so both
    are marked unconfirmed and M4 must refuse to render them.
    """
    assert set(profile.unconfirmed_identity) == {"email", "github"}
    for field in profile.unconfirmed_identity:
        assert profile.identity[field].note, f"{field} is unconfirmed but has no explanation"


@pytest.mark.skipif(
    not ILLUSTRATIVE_SOURCE.exists(), reason="interview-prep repo not present on this machine"
)
def test_no_illustrative_content_leaked_in(profile: MasterProfile) -> None:
    """Assert the banner-carrying files really are excluded as a source."""
    banner_files = list(ILLUSTRATIVE_SOURCE.glob("*.md"))
    assert banner_files, "expected the illustrative answer files to be present"

    profile_text = " ".join(b.text for b in profile.bullets).casefold()
    profile_sentences = {s.strip() for s in re.split(r"[.\n]", profile_text) if len(s.strip()) > 40}

    for path in banner_files:
        content = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for sentence in re.split(r"[.\n]", content):
            candidate = sentence.strip()
            if len(candidate) > 40 and candidate in profile_sentences:
                pytest.fail(f"illustrative sentence from {path.name} leaked into the profile")


def test_all_tools_is_queryable_for_verify_claims(profile: MasterProfile) -> None:
    tools = profile.all_tools()
    assert "python" in tools
    assert "fastapi" in tools
    # The calibration fact that matters: no PyTorch training loop has ever been written,
    # so a generated resume asserting PyTorch must fail verification.
    assert "pytorch" not in tools
    assert "tensorflow" not in tools
