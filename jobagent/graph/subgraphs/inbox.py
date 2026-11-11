"""Inbox watcher -- M7. A separate scheduled graph, not part of the apply run.

    poll_gmail -> classify -> escalate -> digest

`interview_invite` and `assessment_link` escalate to Telegram immediately. Everything
else waits for the 8pm digest. That split is the whole point: an interview invite that
sits in a nightly summary is an interview invite you answered a day late.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from jobagent.graph.state import RunState

__all__ = ["INBOX_CLASSES", "build_inbox_graph"]

INBOX_CLASSES: tuple[str, ...] = (
    "rejection",
    "recruiter_reply",
    "interview_invite",
    "assessment_link",
    "other",
)

#: Classifications that page you the moment they are seen.
URGENT_CLASSES: frozenset[str] = frozenset({"interview_invite", "assessment_link"})


def poll_gmail(state: RunState) -> dict[str, object]:
    """M7: fetch replies from companies present in the applications table."""
    raise NotImplementedError("M7: inbox.poll_gmail")


def classify(state: RunState) -> dict[str, object]:
    """M7: label each thread as one of INBOX_CLASSES via structured output."""
    raise NotImplementedError("M7: inbox.classify")


def escalate(state: RunState) -> dict[str, object]:
    """M7: Telegram immediately for URGENT_CLASSES; interrupt() for anything ambiguous."""
    raise NotImplementedError("M7: inbox.escalate")


def digest(state: RunState) -> dict[str, object]:
    """M7: the 8pm summary -- applied / screened-out / needs-attention."""
    raise NotImplementedError("M7: inbox.digest")


def build_inbox_graph() -> StateGraph[RunState]:
    builder: StateGraph[RunState] = StateGraph(RunState)
    builder.add_node("poll_gmail", poll_gmail)
    builder.add_node("classify", classify)
    builder.add_node("escalate", escalate)
    builder.add_node("digest", digest)

    builder.add_edge(START, "poll_gmail")
    builder.add_edge("poll_gmail", "classify")
    builder.add_edge("classify", "escalate")
    builder.add_edge("escalate", "digest")
    builder.add_edge("digest", END)
    return builder
