"""The root graph.

    load_profile -> plan_searches -> discovery -> screener
                 -> [Send per surviving job] job_pipeline -> tracker -> END

Three details make `get_graph(xray=1).draw_mermaid()` render the whole system today, with
every M2-M7 node still a stub:

1. Subgraphs are added as **compiled graph objects**, not callables. Only then does xray
   expand them into nested Mermaid subgraphs; a `functools.partial` renders as one opaque
   box.
2. The `Send` fan-out declares a `path_map`. Without it Mermaid draws no outbound edges
   from the router, and the diagram silently lies about the topology.
3. `build_graph(checkpointer=None)` compiles without touching Postgres, so `make graph`
   works with the Docker daemon down.

Rendering never executes a node, which is why `NotImplementedError` stubs are harmless
here.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from jobagent.graph.state import JobOutput, JobState, RunState, RunStats
from jobagent.graph.subgraphs.apply import build_apply_graph
from jobagent.graph.subgraphs.discovery import build_discovery_graph
from jobagent.graph.subgraphs.screener import build_screener_graph
from jobagent.graph.subgraphs.tailor import build_tailor_graph
from jobagent.graph.subgraphs.tracker import build_tracker_graph
from jobagent.logging import bind_node

__all__ = ["JOB_PIPELINE", "build_graph", "build_job_pipeline", "render_mermaid"]

JOB_PIPELINE = "job_pipeline"


def load_profile(state: RunState) -> dict[str, object]:
    """M4 will read and version master_profile.yaml here.

    In M1 it simply stamps the version already supplied, so the node exists in the graph
    and the shape is fixed.
    """
    with bind_node("supervisor.load_profile", run_id=state["run_id"]):
        return {"profile_version": state.get("profile_version") or "unversioned"}


def plan_searches(state: RunState) -> dict[str, object]:
    """M2 will expand config into SearchSpecs. M1 passes through whatever was supplied."""
    with bind_node("supervisor.plan_searches", run_id=state["run_id"]):
        return {"search_specs": list(state.get("search_specs", []))}


def fan_out_jobs(state: RunState) -> list[Send] | str:
    """Fan out one branch per job that survived screening.

    Returns the string `"tracker"` when there is nothing to process, so an empty run
    still persists its stats and writes the CSV rather than dead-ending.
    """
    from jobagent.graph.state import JobStatus
    from jobagent.store.transitions import outranks

    pending = [
        job
        for job in state.get("jobs", [])
        if job.status == JobStatus.SCREENED
        or (outranks(job.status, JobStatus.SCREENED) and job.status == JobStatus.TAILORED)
    ]
    if not pending:
        return "tracker"

    return [
        Send(
            JOB_PIPELINE,
            {
                "run_id": state["run_id"],
                "job": job,
                "fit": None,
                "resume": None,
                "cover_letter": None,
                "apply_route": None,
                "attempts": 0,
                "needs_human": False,
                "result": None,
            },
        )
        for job in pending
    ]


def build_job_pipeline() -> CompiledStateGraph[JobState, Any, Any, Any]:
    """tailor -> apply, as one compiled per-job subgraph.

    Its output schema is `JobOutput`, whose keys are *parent* RunState keys. That is what
    lets a Send-fanned branch write back through the parent's reducers -- `jobs` merges by
    fingerprint, `errors` and `escalations` append -- with no collector node.
    """
    builder: StateGraph[JobState, Any, JobState, JobOutput] = StateGraph(
        JobState, output_schema=JobOutput
    )
    builder.add_node("tailor", build_tailor_graph().compile())
    builder.add_node("apply", build_apply_graph().compile())
    builder.add_edge(START, "tailor")
    builder.add_edge("tailor", "apply")
    builder.add_edge("apply", END)
    return builder.compile()


def build_graph(checkpointer: Any = None) -> CompiledStateGraph[RunState, Any, Any, Any]:
    """Compile the root graph.

    `checkpointer=None` is a first-class case, not a degraded one: it is what makes the
    graph renderable without a database.
    """
    builder: StateGraph[RunState] = StateGraph(RunState)

    builder.add_node("load_profile", load_profile)
    builder.add_node("plan_searches", plan_searches)
    # Compiled graph objects, not callables -- see the module docstring.
    builder.add_node("discovery", build_discovery_graph().compile())
    builder.add_node("screener", build_screener_graph().compile())
    builder.add_node(JOB_PIPELINE, build_job_pipeline())
    builder.add_node("tracker", build_tracker_graph().compile())

    builder.add_edge(START, "load_profile")
    builder.add_edge("load_profile", "plan_searches")
    builder.add_edge("plan_searches", "discovery")
    builder.add_edge("discovery", "screener")
    builder.add_conditional_edges("screener", fan_out_jobs, path_map=[JOB_PIPELINE, "tracker"])
    builder.add_edge(JOB_PIPELINE, "tracker")
    builder.add_edge("tracker", END)

    return builder.compile(checkpointer=checkpointer)


def render_mermaid(*, xray: bool = True) -> str:
    """Render the whole system as Mermaid, with no database.

    `xray=True` (not `xray=1`) matters: the per-job pipeline nests two levels deep --
    supervisor > job_pipeline > tailor/apply -- and depth 1 stops before the lane nodes,
    producing a diagram that quietly omits `verify_claims` and `confirm_submission`.
    """
    return build_graph(checkpointer=None).get_graph(xray=xray).draw_mermaid()


def initial_state(run_id: str, *, profile_version: str = "unversioned") -> RunState:
    return {
        "run_id": run_id,
        "profile_version": profile_version,
        "search_specs": [],
        "raw_jobs": [],
        "jobs": [],
        "escalations": [],
        "errors": [],
        "stats": RunStats(),
    }
