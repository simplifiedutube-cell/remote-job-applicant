"""Adapter contracts.

Every job board implements `PortalAdapter`; every applicant-tracking system implements
`ATSAdapter`. Both are runtime-checkable Protocols rather than base classes, so an
adapter is a plain object with no inheritance obligation.

The rule the design has to satisfy: adding a board means writing one file in
`adapters/portals/` and decorating the class. No graph change, no `__init__.py` edit, no
reinstall.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobagent.graph.state import ApplicationResult, Artifact, Job, RawJob, SearchSpec

__all__ = ["ATSAdapter", "PortalAdapter"]


@runtime_checkable
class PortalAdapter(Protocol):
    """A job board."""

    name: str

    def search(self, spec: SearchSpec) -> list[RawJob]:
        """Return verbatim postings. Normalization happens downstream, not here."""
        ...

    def can_apply(self, job: Job) -> bool:
        """Whether this adapter can submit to `job` itself.

        Stubs return False rather than raising: the M5 router calls this on every
        registered adapter to choose a lane, so a raising stub would break routing for
        every other portal the moment M5 lands.
        """
        ...

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        """Submit. Must route every irreversible action through `safety.submit_gate()`."""
        ...


@runtime_checkable
class ATSAdapter(Protocol):
    """A deterministic applicant-tracking-system lane (Greenhouse, Lever, Ashby, Workday)."""

    name: str

    def matches(self, apply_url: str) -> bool:
        """Whether `apply_url` belongs to this ATS. Used by the M5 router."""
        ...

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult: ...
