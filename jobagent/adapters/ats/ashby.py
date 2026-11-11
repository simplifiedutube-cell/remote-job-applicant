"""Ashby ATS adapter -- M5 stub.

Common among 2024+ startups. React-heavy but consistent.

Deterministic Playwright, not a computer-use loop: these boards have stable DOM, which
makes a scripted adapter roughly fifty times cheaper and considerably more reliable.
"""

from __future__ import annotations

from jobagent.adapters.registry import register_ats
from jobagent.graph.state import ApplicationResult, Artifact, Job

HOSTS: tuple[str, ...] = ("ashbyhq.com", "jobs.ashbyhq.com")


@register_ats
class AshbyAdapter:
    name = "ashby"

    def matches(self, apply_url: str) -> bool:
        """Host match. Implemented now because the M5 router needs it to choose a lane."""
        from urllib.parse import urlsplit

        host = (urlsplit(apply_url).hostname or "").removeprefix("www.")
        return any(host == h or host.endswith(f".{h}") for h in HOSTS)

    def apply(self, job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
        raise NotImplementedError("M5: ashby.apply")
