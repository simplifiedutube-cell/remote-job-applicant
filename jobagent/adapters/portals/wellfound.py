"""Wellfound portal adapter -- M2 stub.

Startup-heavy. Requires a browser session; no usable public API.
"""

from __future__ import annotations

from jobagent.adapters.registry import register_portal
from jobagent.graph.state import ApplicationResult, Artifact, Job, RawJob, SearchSpec

BASE_URL = "https://wellfound.com"


@register_portal
class WellfoundAdapter:
    name = "wellfound"
    #: Needs a manually-authenticated Playwright session (M6).
    requires_browser = True

    def search(self, spec: SearchSpec) -> list[RawJob]:
        raise NotImplementedError("M2: wellfound.search")

    def can_apply(self, job: Job) -> bool:
        """False until the apply lane exists.

        Returning False rather than raising is deliberate: the M5 router calls this on
        every registered adapter to pick a lane, so a raising stub would break routing
        for every other portal.
        """
        return False

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        raise NotImplementedError("M5: wellfound.apply")
