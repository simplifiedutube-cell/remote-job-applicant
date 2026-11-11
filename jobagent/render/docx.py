"""Resume rendering -- M4.

docxtpl over `templates/resume.docx.j2`, populated strictly from selected evidence
bullets. The template must contain no free-text field the model can fill with prose of
its own invention; every slot maps to a bullet with an `evidence_id`.
"""

from __future__ import annotations

from pathlib import Path

from jobagent.graph.state import Artifact

__all__ = ["render_resume"]


def render_resume(context: dict[str, object], *, out_dir: Path) -> Artifact:
    raise NotImplementedError("M4: render.docx.render_resume")
