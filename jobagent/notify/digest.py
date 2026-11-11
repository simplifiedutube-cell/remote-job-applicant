"""Daily digest -- M7.

8pm: applied / screened-out / needs-attention. Deliberately separate from escalation:
routine counts belong in a summary, an interview invite does not.
"""

from __future__ import annotations

from datetime import date

__all__ = ["build_digest"]


def build_digest(on: date | None = None) -> str:
    raise NotImplementedError("M7: notify.digest.build_digest")
