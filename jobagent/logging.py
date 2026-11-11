"""Structured logging.

Spec requirement: every node logs `run_id`, `job_fingerprint`, `node`, `duration_ms`.
`bind_node()` is the one helper that guarantees it -- including on the failure path,
which is the case you actually want the timing for.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

__all__ = ["bind_node", "configure_logging"]


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


@contextmanager
def bind_node(
    node: str,
    *,
    run_id: str | None = None,
    job_fingerprint: str | None = None,
    **extra: Any,
) -> Iterator[structlog.BoundLogger]:
    """Bind node context, and emit `node_finished` with `duration_ms` on the way out.

    The `finally` is deliberate: a node that raised is exactly the one whose duration and
    identity you want in the log.
    """
    structlog.contextvars.bind_contextvars(
        node=node, run_id=run_id, job_fingerprint=job_fingerprint, **extra
    )
    log = structlog.get_logger(node)
    started = time.perf_counter()
    log.debug("node_started")
    try:
        yield log
    except Exception as exc:
        log.error(
            "node_failed",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    else:
        log.info("node_finished", duration_ms=round((time.perf_counter() - started) * 1000, 2))
    finally:
        structlog.contextvars.unbind_contextvars("node", "run_id", "job_fingerprint", *extra.keys())
