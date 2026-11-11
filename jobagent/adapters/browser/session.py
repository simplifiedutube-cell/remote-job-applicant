"""Authenticated browser sessions -- M6.

**Credentials are never stored, never typed, and never read by this program.**
`jobagent login <portal>` opens a *headed* browser, you log in by hand, and only the
resulting cookies are persisted to `secrets/storage_state/<portal>.json` (gitignored).

Session expiry is routine rather than exceptional. `is_stale()` exists so the apply lane
escalates to you instead of silently failing every job in a batch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobagent.config import get_config

__all__ = ["is_stale", "login", "storage_state_path"]

#: Portals invalidate sessions aggressively; treat anything older than this as suspect.
MAX_SESSION_AGE = timedelta(days=7)


def storage_state_path(portal: str) -> Path:
    return get_config().storage_state_dir / f"{portal}.json"


def is_stale(portal: str, *, max_age: timedelta = MAX_SESSION_AGE) -> bool:
    """True when the stored session is missing or older than `max_age`."""
    path = storage_state_path(portal)
    if not path.exists():
        return True
    age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return age > max_age


def login(portal: str) -> Path:
    """M6: open a headed browser, wait for the human, persist cookies only."""
    raise NotImplementedError("M6: browser.session.login")
