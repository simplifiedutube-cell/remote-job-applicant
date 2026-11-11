"""Evidence retrieval over master_profile.yaml -- M4.

Bullets are embedded once per profile version and retrieved top-k per job description.
This is the "selection" half of the no-fabrication rule: the resume writer may only
choose and rephrase what comes back from here.
"""

from __future__ import annotations

from collections.abc import Sequence

from jobagent.profile.schema import EvidenceBullet

__all__ = ["embed_profile", "retrieve"]


def embed_profile(profile_version: str) -> int:
    """M4: embed every bullet into jobagent.profile_evidence. Returns rows written."""
    raise NotImplementedError("M4: profile.evidence.embed_profile")


def retrieve(job_description: str, *, k: int = 12) -> Sequence[EvidenceBullet]:
    """M4: top-k bullets by cosine similarity against the JD."""
    raise NotImplementedError("M4: profile.evidence.retrieve")
