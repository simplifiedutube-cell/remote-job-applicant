"""The dry-run choke point.

The stated threat model is "a node author forgets to check the flag". The response is
that **no node ever checks the flag**. Instead:

1. `RunConfig.dry_run` defaults to True, and the CLI exposes `--live`, never `--dry-run`.
2. `--live` is honoured only when `JOBAGENT_ALLOW_LIVE=1` is *also* set in the
   environment. A flag alone cannot go live; a stray env var alone cannot go live. This
   is the only place in the codebase where that conjunction is computed.
3. Every irreversible action goes through `submit_gate()`, which raises `DryRunBlocked`.
4. A mechanical backstop sits at the I/O boundary (`adapters/browser/safe_page.py`), so
   code that has never heard of this module still cannot POST.

And in the database, `applications_one_live_submit_uidx` makes a second live submit a
`UniqueViolation` even if every layer above somehow fails.

The mode is carried in a `ContextVar` as well as in the graph config. The ContextVar
exists so the Playwright wrapper -- five frames below any node -- can consult it without
every intermediate signature having to thread it through.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog

from jobagent.config import RunConfig, get_config

__all__ = [
    "DryRunBlocked",
    "LiveModeRefused",
    "current_mode",
    "live_allowed",
    "resolve_mode",
    "run_mode",
    "submit_gate",
]

log = structlog.get_logger(__name__)

#: True == live. Default False so that an unconfigured process is incapable of submitting.
_LIVE: ContextVar[bool] = ContextVar("jobagent_live_mode", default=False)


class DryRunBlocked(RuntimeError):
    """Raised when an irreversible action is attempted in dry-run mode.

    This is a normal, expected control-flow signal during a dry run -- the apply lanes
    catch it and record `ApplicationResult(status="skipped_dry_run")`.
    """


class LiveModeRefused(RuntimeError):
    """Raised when live mode was requested but the second key was not present."""


def live_allowed(config: RunConfig | None = None) -> bool:
    """The two-key rule, computed in exactly one place.

    Returns True only when the operator has both cleared `dry_run` *and* set
    `JOBAGENT_ALLOW_LIVE=1`. Everything else -- including `--live` on its own -- is a
    dry run.
    """
    cfg = config or get_config()
    return (not cfg.dry_run) and cfg.allow_live


def resolve_mode(*, requested_live: bool, config: RunConfig | None = None) -> bool:
    """Resolve the effective mode for a run, refusing an unsatisfiable live request.

    Refusing loudly matters: silently downgrading `--live` to a dry run would leave you
    believing applications were sent when nothing was.
    """
    cfg = config or get_config()
    if not requested_live:
        return False
    if not cfg.allow_live:
        raise LiveModeRefused(
            "--live requires JOBAGENT_ALLOW_LIVE=1 in the environment as a second key. "
            "Refusing to run: a live request that silently became a dry run would be "
            "worse than an error."
        )
    return True


@contextmanager
def run_mode(live: bool) -> Iterator[bool]:
    """Bind the effective mode for the duration of a run."""
    if live:
        log.warning(
            "live_mode_enabled",
            detail="applications WILL be submitted for real in this run",
        )
    token = _LIVE.set(live)
    try:
        yield live
    finally:
        _LIVE.reset(token)


def current_mode() -> bool:
    """True when the current context is live. Defaults to False everywhere else."""
    return _LIVE.get()


def submit_gate(action: str, *, job_fingerprint: str | None = None, url: str | None = None) -> None:
    """Call immediately before any irreversible action. Raises in dry-run mode.

    `action` is a short verb phrase for the log and the exception message, e.g.
    "click submit on greenhouse form".
    """
    if current_mode():
        log.warning(
            "live_action_permitted",
            action=action,
            job_fingerprint=job_fingerprint,
            url=url,
        )
        return

    log.info("dry_run_blocked", action=action, job_fingerprint=job_fingerprint, url=url)
    raise DryRunBlocked(f"dry-run: refused to {action}")
