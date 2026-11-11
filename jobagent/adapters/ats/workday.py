"""Workday ATS adapter -- M5 stub.

Enterprise. Multi-step wizard with per-tenant variation -- the hardest of the four,
    and the most likely to need an escalation mid-flow.

Deterministic Playwright, not a computer-use loop: these boards have stable DOM, which
makes a scripted adapter roughly fifty times cheaper and considerably more reliable.
"""

from __future__ import annotations

from jobagent.adapters.registry import register_ats
from jobagent.graph.state import ApplicationResult, Artifact, Job

HOSTS: tuple[str, ...] = ("myworkdayjobs.com", "wd1.myworkdayjobs.com", "wd5.myworkdayjobs.com")


@register_ats
class WorkdayAdapter:
    name = "workday"

    def matches(self, apply_url: str) -> bool:
        """Host match. Implemented now because the M5 router needs it to choose a lane."""
        from urllib.parse import urlsplit

        host = (urlsplit(apply_url).hostname or "").removeprefix("www.")
        return any(host == h or host.endswith(f".{h}") for h in HOSTS)

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        raise NotImplementedError("M5: workday.apply")
