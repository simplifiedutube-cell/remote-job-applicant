"""Telegram escalation -- M7.

Plain HTTPS against the Bot API; no SDK needed. Used for `interview_invite` and
`assessment_link`, which must reach you immediately rather than waiting for the digest.
"""

from __future__ import annotations

__all__ = ["send"]

API_BASE = "https://api.telegram.org"


def send(text: str, *, silent: bool = False) -> bool:
    """M7: POST sendMessage. Returns whether it was delivered."""
    raise NotImplementedError("M7: notify.telegram.send")
