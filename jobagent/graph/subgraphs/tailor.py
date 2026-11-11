"""Resume subgraph -- M4.

    extract_requirements -> select_evidence -> render -> verify_claims

`verify_claims` is the reason this subgraph exists in this shape. A generative resume
writer will eventually assert a framework you have never touched, and you find out in the
interview. That node re-reads the generated document, extracts every factual claim
(tool, years, employer, metric), and checks each against `master_profile.yaml`.

**It fails closed.** Any unsupported claim sends the job to the manual queue rather than
downgrading to a warning, because a warning in a batch run is a warning nobody reads.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from jobagent.graph.state import JobOutput, JobState

__all__ = ["build_tailor_graph"]


def extract_requirements(state: JobState) -> dict[str, object]:
    """M4: structured parse of the JD into required/preferred skills, years, keywords."""
    raise NotImplementedError("M4: tailor.extract_requirements")


def select_evidence(state: JobState) -> dict[str, object]:
    """M4: top-k bullets from master_profile.yaml via pgvector. Selection, never invention."""
    raise NotImplementedError("M4: tailor.select_evidence")


def render(state: JobState) -> dict[str, object]:
    """M4: docxtpl -> .docx, then LibreOffice headless -> .pdf.

    `soffice` is absent on this machine, so `render/pdf.py` falls back to a NullConverter
    that emits the .docx alone and marks `meta.pdf_status="unavailable"`. Greenhouse,
    Lever and Ashby all accept .docx uploads, so this is degraded, not blocked.
    """
    raise NotImplementedError("M4: tailor.render")


def verify_claims(state: JobState) -> dict[str, object]:
    """M4: diff every claim in the rendered resume against master_profile.yaml.

    FAILS CLOSED. Unsupported claim -> manual queue, never a soft warning.
    """
    raise NotImplementedError("M4: tailor.verify_claims")


def build_tailor_graph() -> StateGraph[JobState, Any, JobState, JobOutput]:
    builder: StateGraph[JobState, Any, JobState, JobOutput] = StateGraph(
        JobState, output_schema=JobOutput
    )
    builder.add_node("extract_requirements", extract_requirements)
    builder.add_node("select_evidence", select_evidence)
    builder.add_node("render", render)
    builder.add_node("verify_claims", verify_claims)

    builder.add_edge(START, "extract_requirements")
    builder.add_edge("extract_requirements", "select_evidence")
    builder.add_edge("select_evidence", "render")
    builder.add_edge("render", "verify_claims")
    builder.add_edge("verify_claims", END)
    return builder
