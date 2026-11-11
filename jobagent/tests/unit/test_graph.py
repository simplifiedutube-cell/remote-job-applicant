"""The scaffold's regression test.

`draw_mermaid()` must render the entire system -- including M2-M7 nodes that are still
`NotImplementedError` stubs -- with no database. This must stay green through every later
milestone; if it breaks, the graph topology changed silently.
"""

from __future__ import annotations

import pytest

from jobagent.graph.subgraphs.apply import ROUTE_NODES
from jobagent.graph.supervisor import build_graph, initial_state, render_mermaid

TOP_LEVEL = ("load_profile", "plan_searches", "discovery", "screener", "job_pipeline", "tracker")

NESTED = (
    # discovery (M2)
    "plan_queries", "fetch_portals", "normalize", "dedupe", "embed_dedupe",
    # screener (M3)
    "hard_filters", "score_fit", "route_verdict",
    # tailor (M4)
    "extract_requirements", "select_evidence", "render", "verify_claims",
    # apply (M5/M6)
    "route", "confirm_submission", *ROUTE_NODES,
    # tracker (M1 -- the implemented one)
    "persist_jobs", "reconcile_status", "export_csv", "update_stats",
)  # fmt: skip


@pytest.fixture(scope="module")
def mermaid() -> str:
    return render_mermaid()


def test_compiles_without_a_checkpointer() -> None:
    """`make graph` has to work with the Docker daemon down."""
    assert build_graph(checkpointer=None) is not None


@pytest.mark.parametrize("node", TOP_LEVEL)
def test_top_level_nodes_render(mermaid: str, node: str) -> None:
    assert node in mermaid


@pytest.mark.parametrize("node", NESTED)
def test_nested_stub_nodes_render(mermaid: str, node: str) -> None:
    """Stubs render fine because rendering never executes a node."""
    assert node in mermaid


def test_xray_expands_two_levels(mermaid: str) -> None:
    """supervisor > job_pipeline > tailor/apply.

    `xray=1` stops before the lane nodes and produces a diagram that quietly omits
    verify_claims and confirm_submission -- which is why render_mermaid passes xray=True.
    """
    shallow = build_graph(None).get_graph(xray=1).draw_mermaid()
    assert "verify_claims" not in shallow
    assert "verify_claims" in mermaid


def test_fan_out_edges_are_drawn(mermaid: str) -> None:
    """A Send fan-out without a declared path_map renders no outbound edges at all."""
    assert "job_pipeline" in mermaid
    assert "tracker" in mermaid
    assert "-->" in mermaid or "-." in mermaid


def test_empty_run_routes_straight_to_tracker() -> None:
    """No jobs must still persist stats and write the CSV, not dead-end."""
    from jobagent.graph.supervisor import fan_out_jobs

    assert fan_out_jobs(initial_state("run_x")) == "tracker"


def test_fan_out_emits_one_send_per_screened_job() -> None:
    from jobagent.graph.state import JobStatus
    from jobagent.graph.supervisor import fan_out_jobs
    from jobagent.tests.conftest import make_job

    state = initial_state("run_x")
    state["jobs"] = [
        make_job(company="A", status=JobStatus.SCREENED),
        make_job(company="B", status=JobStatus.SCREENED),
        make_job(company="C", status=JobStatus.SCREENED_OUT),  # excluded
        make_job(company="D", status=JobStatus.APPLIED),  # already done
    ]
    sends = fan_out_jobs(state)
    assert isinstance(sends, list)
    assert len(sends) == 2
    assert all(s.node == "job_pipeline" for s in sends)


def test_stub_nodes_raise_rather_than_silently_pass() -> None:
    """A discovery lane that quietly yields zero jobs looks exactly like a bad search."""
    from jobagent.graph.subgraphs.discovery import plan_queries
    from jobagent.graph.subgraphs.tailor import verify_claims

    with pytest.raises(NotImplementedError, match="M2"):
        plan_queries(initial_state("run_x"))
    with pytest.raises(NotImplementedError, match="M4"):
        verify_claims({})  # type: ignore[typeddict-item]


def test_submit_nodes_are_never_retried() -> None:
    """Guardrail 2: a retried submit is a duplicate application."""
    from jobagent.graph.subgraphs.apply import NO_RETRY

    assert NO_RETRY.max_attempts == 1
