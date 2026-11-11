"""Per-domain token bucket and human-ish pacing.

Guardrail 6: rate limits live in the adapter layer, not sprinkled through nodes. The
`RateLimited` proxy is what `registry.get_portal()` hands back, so there is no supported
way for a node to obtain an unthrottled adapter.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import structlog

from jobagent.config import get_config

__all__ = ["RateLimited", "TokenBucket", "domain_of", "pace"]

log = structlog.get_logger(__name__)


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "unknown").removeprefix("www.")


@dataclass
class TokenBucket:
    """Classic token bucket. `capacity` tokens, refilled at `refill_per_second`."""

    capacity: float
    refill_per_second: float
    _tokens: float = field(init=False)
    _updated: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._updated = time.monotonic()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until `tokens` are available. Returns seconds waited."""
        with self._lock:
            waited = 0.0
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.refill_per_second
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = (tokens - self._tokens) / self.refill_per_second
                time.sleep(deficit)
                waited += deficit


_BUCKETS: dict[str, TokenBucket] = {}
_BUCKETS_LOCK = threading.Lock()


def bucket_for(domain: str) -> TokenBucket:
    with _BUCKETS_LOCK:
        if domain not in _BUCKETS:
            # Conservative default: 1 request per 2s, bursts of 5.
            _BUCKETS[domain] = TokenBucket(capacity=5.0, refill_per_second=0.5)
        return _BUCKETS[domain]


def pace() -> float:
    """Sleep a randomized human-ish interval. Returns the seconds slept.

    Used between submits, not between page reads. Randomized because a fixed interval is
    itself a fingerprint.
    """
    cfg = get_config()
    delay = random.uniform(cfg.pace_min_seconds, cfg.pace_max_seconds)  # noqa: S311
    log.info("pacing", seconds=round(delay, 1))
    time.sleep(delay)
    return delay


class RateLimited:
    """Transparent proxy that spends a token before any adapter call that touches network.

    Wrapping rather than subclassing keeps the adapter contract free of any obligation to
    remember rate limiting -- an adapter author literally cannot forget it.
    """

    _THROTTLED = frozenset({"search", "apply", "fetch", "can_apply"})

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return str(self._inner.name)

    def __getattr__(self, item: str) -> Any:
        attr = getattr(self._inner, item)
        if item not in self._THROTTLED or not callable(attr):
            return attr

        def throttled(*args: Any, **kwargs: Any) -> Any:
            waited = bucket_for(self.name).acquire()
            if waited:
                log.debug("rate_limited", adapter=self.name, method=item, waited_s=round(waited, 2))
            return attr(*args, **kwargs)

        return throttled

    def __repr__(self) -> str:
        return f"RateLimited({self._inner!r})"
