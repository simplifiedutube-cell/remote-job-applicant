"""Schema for `master_profile.yaml` -- the only permitted source of resume claims.

Every bullet carries a stable `evidence_id`, an `employer`, and structured `facts`. The
structure is the point: `verify_claims` needs something machine-checkable to diff a
generated resume against, and prose cannot be diffed reliably. A claim about a tool is
checkable against `facts.tools`; a claim about a number is checkable against
`facts.metrics`.

`confirmed: false` on an identity field means the underlying source was ambiguous. M4
must refuse to emit an unconfirmed field rather than pick one -- getting a GitHub handle
wrong on a resume is a self-inflicted credibility problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Employment",
    "EvidenceBullet",
    "IdentityField",
    "MasterProfile",
    "Metric",
    "load_profile",
    "profile_path",
]

Section = Literal["summary", "experience", "project", "skill", "education"]


class Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class IdentityField(Strict):
    value: str
    #: False when the source material was contradictory. M4 must not emit these.
    confirmed: bool = True
    note: str | None = None


class Metric(Strict):
    """One quantified claim, in the form `verify_claims` can check."""

    name: str
    value: float
    unit: Literal["percent", "count", "currency", "days", "hours", "ratio"] = "percent"
    direction: Literal["increase", "decrease", "absolute"] = "absolute"
    #: How this number was arrived at. Empty means you cannot defend it in an interview.
    basis: str | None = None


class EvidenceBullet(Strict):
    evidence_id: str = Field(min_length=1)
    section: Section
    text: str = Field(min_length=1)
    employer: str | None = None
    tools: tuple[str, ...] = ()
    metrics: tuple[Metric, ...] = ()
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _experience_needs_employer(self) -> Self:
        if self.section == "experience" and not self.employer:
            raise ValueError(f"{self.evidence_id}: experience bullets must name an employer")
        return self


class Employment(Strict):
    employer: str
    title: str
    start: str  # YYYY-MM
    end: str | None = None  # None == present
    location: str | None = None
    employment_type: Literal["full_time", "contract", "freelance", "part_time"] = "full_time"
    summary: str | None = None


class MasterProfile(Strict):
    version: str
    identity: dict[str, IdentityField]
    headline: str
    education: tuple[str, ...] = ()
    employment: tuple[Employment, ...] = ()
    bullets: tuple[EvidenceBullet, ...] = ()

    @model_validator(mode="after")
    def _ids_unique_and_employers_known(self) -> Self:
        seen: set[str] = set()
        for bullet in self.bullets:
            if bullet.evidence_id in seen:
                raise ValueError(f"duplicate evidence_id {bullet.evidence_id!r}")
            seen.add(bullet.evidence_id)

        known = {e.employer for e in self.employment}
        for bullet in self.bullets:
            if bullet.section == "experience" and bullet.employer not in known:
                raise ValueError(
                    f"{bullet.evidence_id}: employer {bullet.employer!r} is not in employment"
                )
        return self

    @property
    def unconfirmed_identity(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, v in self.identity.items() if not v.confirmed))

    def bullet(self, evidence_id: str) -> EvidenceBullet:
        for b in self.bullets:
            if b.evidence_id == evidence_id:
                return b
        raise KeyError(evidence_id)

    def all_tools(self) -> frozenset[str]:
        """Every tool the profile can support a claim about. verify_claims checks here."""
        return frozenset(t.casefold() for b in self.bullets for t in b.tools)


def profile_path() -> Path:
    return Path(__file__).parent / "master_profile.yaml"


def load_profile(path: Path | None = None) -> MasterProfile:
    raw = yaml.safe_load((path or profile_path()).read_text(encoding="utf-8"))
    return MasterProfile.model_validate(raw)
