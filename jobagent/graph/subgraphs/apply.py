"""Apply subgraph -- M5 (deterministic lanes) and M6 (browser agent).

    route -> {portal_form | linkedin_easy | external_ats | browser_agent | manual}
          -> confirm_submission

Routing, per spec:

    linkedin + easy_apply       -> linkedin_easy   (browser agent)
    linkedin + external link    -> follow, detect ATS, recurse
    apply_url matches known ATS -> external_ats    (deterministic playwright)
    portal has native apply     -> portal_form     (deterministic playwright)
    unknown                     -> browser_agent   (computer use)
    any node needs_human        -> manual queue

Every lane terminates at `confirm_submission`, which captures a screenshot and the
confirmation text. No confirmation means `ApplicationResult(status="uncertain")` -- and
that is not a convention, it is enforced: the model validator refuses to construct
`status="applied"` without evidence.

Submit nodes get `RetryPolicy(max_attempts=1)` when wired. Retrying a submit is how you
apply twice.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from jobagent.graph.state import ApplyRoute, JobOutput, JobState

__all__ = ["ROUTE_NODES", "build_apply_graph"]

#: Lane node names, in the order the router considers them.
ROUTE_NODES: tuple[str, ...] = (
    "portal_form",
    "linkedin_easy",
    "external_ats",
    "browser_agent",
    "manual",
)

#: Guardrail 2: a submit is never retried. A network blip that retried a submit would
#: produce two applications, which is precisely the failure this system must not have.
NO_RETRY = RetryPolicy(max_attempts=1)


def route(state: JobState) -> dict[str, object]:
    """M5: choose a lane from apply_url, ATS detection, and adapter.can_apply()."""
    raise NotImplementedError("M5: apply.route")


def portal_form(state: JobState) -> dict[str, object]:
    """M5: deterministic Playwright fill of a portal's native application form."""
    raise NotImplementedError("M5: apply.portal_form")


def external_ats(state: JobState) -> dict[str, object]:
    """M5: deterministic ATS adapter (Greenhouse and Lever first -- highest coverage)."""
    raise NotImplementedError("M5: apply.external_ats")


def linkedin_easy(state: JobState) -> dict[str, object]:
    """M6: LinkedIn Easy Apply via the browser agent and a manually-authenticated session."""
    raise NotImplementedError("M6: apply.linkedin_easy")


def browser_agent(state: JobState) -> dict[str, object]:
    """M6: computer-use lane for genuinely unknown sites.

    Low-confidence free-text answers must `interrupt()` rather than guess.
    """
    raise NotImplementedError("M6: apply.browser_agent")


def manual(state: JobState) -> dict[str, object]:
    """M5: dead-letter into the manual queue. Nothing silently vanishes."""
    raise NotImplementedError("M5: apply.manual")


def confirm_submission(state: JobState) -> dict[str, object]:
    """M5: capture screenshot + confirmation text + final URL, or record `uncertain`."""
    raise NotImplementedError("M5: apply.confirm_submission")


def _select_lane(state: JobState) -> str:
    """Conditional-edge function. A declared path_map is required for Mermaid to draw edges."""
    route_value = state.get("apply_route")
    if route_value is None:
        return "manual"
    return ApplyRoute(route_value).value


def build_apply_graph() -> StateGraph[JobState, Any, JobState, JobOutput]:
    builder: StateGraph[JobState, Any, JobState, JobOutput] = StateGraph(
        JobState, output_schema=JobOutput
    )
    builder.add_node("route", route)
    builder.add_node("portal_form", portal_form, retry_policy=NO_RETRY)
    builder.add_node("linkedin_easy", linkedin_easy, retry_policy=NO_RETRY)
    builder.add_node("external_ats", external_ats, retry_policy=NO_RETRY)
    builder.add_node("browser_agent", browser_agent, retry_policy=NO_RETRY)
    builder.add_node("manual", manual)
    builder.add_node("confirm_submission", confirm_submission, retry_policy=NO_RETRY)

    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", _select_lane, path_map=list(ROUTE_NODES))
    for lane in ROUTE_NODES:
        builder.add_edge(lane, "confirm_submission")
    builder.add_edge("confirm_submission", END)
    return builder
