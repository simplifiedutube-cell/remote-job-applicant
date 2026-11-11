"""Naukri portal adapter -- M2 stub.

India-specific. HARD-FILTERED to remote + freelance per spec.

        Naukri's terms prohibit automated access. This is a personal, human-paced job
        search rather than scraping at scale, but account restriction is a real risk:
        reuse a manually-authenticated session, never script the login, and keep the
        daily cap low.
"""

from __future__ import annotations

from jobagent.adapters.registry import register_portal
from jobagent.graph.state import ApplicationResult, Artifact, Job, RawJob, SearchSpec

BASE_URL = "https://www.naukri.com"


@register_portal
class NaukriAdapter:
    name = "naukri"
    #: Needs a manually-authenticated Playwright session (M6).
    requires_browser = True

    def search(self, spec: SearchSpec) -> list[RawJob]:
        raise NotImplementedError("M2: naukri.search")

    def can_apply(self, job: Job) -> bool:
        """False until the apply lane exists.

        Returning False rather than raising is deliberate: the M5 router calls this on
        every registered adapter to pick a lane, so a raising stub would break routing
        for every other portal.
        """
        return False

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        raise NotImplementedError("M5: naukri.apply")
