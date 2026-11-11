"""WeWorkRemotely portal adapter -- M2 stub.

RSS feed parsed with feedparser. No auth, no browser.
"""

from __future__ import annotations

from jobagent.adapters.registry import register_portal
from jobagent.graph.state import ApplicationResult, Artifact, Job, RawJob, SearchSpec

BASE_URL = "https://weworkremotely.com/remote-jobs.rss"


@register_portal
class WeWorkRemotelyAdapter:
    name = "weworkremotely"
    #: Plain HTTP; no browser required.
    requires_browser = False

    def search(self, spec: SearchSpec) -> list[RawJob]:
        raise NotImplementedError("M2: weworkremotely.search")

    def can_apply(self, job: Job) -> bool:
        """False until the apply lane exists.

        Returning False rather than raising is deliberate: the M5 router calls this on
        every registered adapter to pick a lane, so a raising stub would break routing
        for every other portal.
        """
        return False

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        raise NotImplementedError("M5: weworkremotely.apply")
