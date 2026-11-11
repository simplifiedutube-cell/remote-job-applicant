"""Screener subgraph -- M3.

    hard_filters -> score_fit -> route_verdict

The screener exists so that tailoring -- the expensive step -- never runs on a job you
would have rejected anyway. Hard filters are deterministic and run first precisely so
that most rejections cost no LLM tokens at all.

Requirement carried into the stubs: a job below threshold is written to `fit_verdicts`
with its score and rationale and moved to `screened_out`. It is never silently dropped.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from jobagent.graph.state import RunState

__all__ = ["build_screener_graph"]


def hard_filters(state: RunState) -> dict[str, object]:
    """M3: deterministic config filters -- comp floor, timezone overlap, seniority, keywords."""
    raise NotImplementedError("M3: screener.hard_filters")


def score_fit(state: RunState) -> dict[str, object]:
    """M3: LLM fit score as structured output -> FitVerdict(score, rationale, blocking_reasons).

    Must use tool-use/structured output rather than prompt-and-parse: `FitVerdict` has a
    validator rejecting a contradictory verdict, and free-text parsing would trip it
    randomly rather than never.
    """
    raise NotImplementedError("M3: screener.score_fit")


def route_verdict(state: RunState) -> dict[str, object]:
    """M3: persist every verdict; below threshold -> screened_out with a recorded reason."""
    raise NotImplementedError("M3: screener.route_verdict")


def build_screener_graph() -> StateGraph[RunState]:
    builder: StateGraph[RunState] = StateGraph(RunState)
    builder.add_node("hard_filters", hard_filters)
    builder.add_node("score_fit", score_fit)
    builder.add_node("route_verdict", route_verdict)

    builder.add_edge(START, "hard_filters")
    builder.add_edge("hard_filters", "score_fit")
    builder.add_edge("score_fit", "route_verdict")
    builder.add_edge("route_verdict", END)
    return builder
