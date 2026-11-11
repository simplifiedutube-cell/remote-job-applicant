"""The pre-expensive-node idempotency check.

One decorator, applied at each subgraph entry, rather than a status check sprinkled
through every node. Combined with `applications_one_live_submit_uidx` as the last line of
defence, killing a run and calling `jobagent resume <run_id>` re-crawls nothing and
re-applies nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import structlog
from langgraph.graph import END
from langgraph.types import Command

from jobagent.graph.state import JobState, JobStatus
from jobagent.store.repo import JobRepo
from jobagent.store.session import session_scope

__all__ = ["idempotent"]

log = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def idempotent(*, skip_if_status_at_or_above: JobStatus) -> Callable[[F], F]:
    """Short-circuit a subgraph whose work is already recorded as done.

    Reads the *persisted* status by fingerprint -- not the in-memory state -- because the
    whole point is to survive a process that died between the database write and the
    checkpoint.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(state: JobState, *args: Any, **kwargs: Any) -> Any:
            job = state["job"]
            try:
                with session_scope() as session:
                    done = JobRepo(session).already_at_or_beyond(
                        job.fingerprint, skip_if_status_at_or_above
                    )
            except Exception as exc:
                # A database that is unreachable must not silently disable the guard;
                # fail the node instead so the run stops rather than double-applying.
                log.error("idempotency_check_failed", error=repr(exc))
                raise

            if done:
                log.info(
                    "skipped_already_done",
                    job_fingerprint=job.fingerprint,
                    floor=skip_if_status_at_or_above.value,
                    node=func.__name__,
                )
                return Command(goto=END)
            return func(state, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
