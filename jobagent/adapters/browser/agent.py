"""Computer-use lane -- M6.

Reserved for genuinely unknown sites. Everything with a stable DOM should go through a
deterministic adapter in `adapters/ats/` instead: cheaper, faster, and far less likely to
do something surprising on a page that takes real actions.

Any free-text question the model is not confident about must `interrupt()` and ask you.
Guessing an answer on a job application is not a recoverable error.
"""

from __future__ import annotations

from jobagent.graph.state import ApplicationResult, Artifact, Job

__all__ = ["CONFIDENCE_THRESHOLD", "answer_question", "apply_with_agent"]

#: Below this, escalate rather than answer.
CONFIDENCE_THRESHOLD = 0.85


def answer_question(question: str, context: dict[str, str]) -> tuple[str, float]:
    """M6: propose an answer and a confidence. Caller escalates below the threshold."""
    raise NotImplementedError("M6: browser.agent.answer_question")


def apply_with_agent(job: Job, resume: Artifact, answers: dict[str, str]) -> ApplicationResult:
    """M6: drive an unknown application form via computer use, through SafePage only."""
    raise NotImplementedError("M6: browser.agent.apply_with_agent")
