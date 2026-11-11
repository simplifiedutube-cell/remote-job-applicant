"""Discovery subgraph -- M2.

    plan_queries -> fetch_portals (Send per SearchSpec) -> normalize -> dedupe -> embed_dedupe

Every node here is a stub. They are typed, importable, and render in Mermaid today; each
raises `NotImplementedError` rather than returning a silent no-op, because a discovery
lane that quietly yields zero jobs looks exactly like a bad search.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from jobagent.graph.state import RunState

__all__ = ["build_discovery_graph"]


def plan_queries(state: RunState) -> dict[str, object]:
    """M2: expand config into concrete SearchSpecs, one per (portal, query)."""
    raise NotImplementedError("M2: discovery.plan_queries")


def fetch_portals(state: RunState) -> dict[str, object]:
    """M2: fan out one Send per SearchSpec; each adapter returns verbatim RawJobs."""
    raise NotImplementedError("M2: discovery.fetch_portals")


def normalize(state: RunState) -> dict[str, object]:
    """M2: RawJob -> Job, computing the fingerprint via jobagent.normalize."""
    raise NotImplementedError("M2: discovery.normalize")


def dedupe(state: RunState) -> dict[str, object]:
    """M2: exact dedupe by fingerprint (mostly free -- it is the jobs primary key)."""
    raise NotImplementedError("M2: discovery.dedupe")


def embed_dedupe(state: RunState) -> dict[str, object]:
    """M2: catch reposts the fingerprint misses, at cosine similarity > 0.92."""
    raise NotImplementedError("M2: discovery.embed_dedupe")


def build_discovery_graph() -> StateGraph[RunState]:
    builder: StateGraph[RunState] = StateGraph(RunState)
    builder.add_node("plan_queries", plan_queries)
    builder.add_node("fetch_portals", fetch_portals)
    builder.add_node("normalize", normalize)
    builder.add_node("dedupe", dedupe)
    builder.add_node("embed_dedupe", embed_dedupe)

    builder.add_edge(START, "plan_queries")
    builder.add_edge("plan_queries", "fetch_portals")
    builder.add_edge("fetch_portals", "normalize")
    builder.add_edge("normalize", "dedupe")
    builder.add_edge("dedupe", "embed_dedupe")
    builder.add_edge("embed_dedupe", END)
    return builder
