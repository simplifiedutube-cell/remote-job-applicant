"""Adapter registry: decorator + package autodiscovery.

Adding a job board is one file plus one decorator. Nothing else.

Rejected alternatives:

* **Entry points.** Requires `uv sync` after every new adapter -- the wrong ergonomics
  for "drop in a file".
* **An explicit import list in `__init__.py`.** That *is* the registration step this
  design exists to remove.

`autodiscover()` records import failures in `_BROKEN` instead of propagating them. A
syntax error in one half-written adapter must not take discovery down for every other
portal; the failure surfaces when that specific adapter is requested, and in `doctor`.

Rate limiting is applied here rather than in nodes (guardrail 6): `get_portal()` returns
the adapter wrapped in a `RateLimited` proxy, so a node author cannot obtain an
unthrottled adapter even by accident.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any, cast

import structlog

from jobagent.adapters.base import ATSAdapter, PortalAdapter

__all__ = [
    "UnknownAdapter",
    "all_ats",
    "all_portals",
    "autodiscover",
    "broken_adapters",
    "get_ats",
    "get_portal",
    "register_ats",
    "register_portal",
]

log = structlog.get_logger(__name__)

_PORTALS: dict[str, type[PortalAdapter]] = {}
_ATS: dict[str, type[ATSAdapter]] = {}
_BROKEN: dict[str, str] = {}


class UnknownAdapter(KeyError):
    """Raised when an adapter name does not resolve."""


def _register(registry: dict[str, type[Any]], cls: type[Any], kind: str) -> type[Any]:
    name = getattr(cls, "name", "")
    if not name or not isinstance(name, str):
        raise ValueError(f"{cls.__name__} must define a non-empty string `name` to be a {kind}")
    if name in registry and registry[name] is not cls:
        raise ValueError(
            f"duplicate {kind} name {name!r}: {registry[name].__name__} and {cls.__name__}"
        )
    registry[name] = cls
    return cls


def register_portal[T](cls: type[T]) -> type[T]:
    return _register(_PORTALS, cls, "portal")


def register_ats[T](cls: type[T]) -> type[T]:
    return _register(_ATS, cls, "ats")


def autodiscover(package: ModuleType) -> None:
    """Import every submodule of `package` so the decorators fire."""
    for info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            _BROKEN[info.name] = repr(exc)
            log.error("adapter_import_failed", module=info.name, error=repr(exc))


def _resolve(registry: dict[str, type[Any]], name: str, kind: str) -> Any:
    if name not in registry:
        raise UnknownAdapter(
            f"unknown {kind} {name!r}; known={sorted(registry)}; broken={sorted(_BROKEN)}"
        )
    return registry[name]()


def get_portal(name: str) -> PortalAdapter:
    from jobagent.adapters.ratelimit import RateLimited

    # The proxy satisfies the protocol at runtime via __getattr__, which a structural
    # type check cannot see; the cast is the honest way to say so.
    return cast("PortalAdapter", RateLimited(_resolve(_PORTALS, name, "portal")))


def get_ats(name: str) -> ATSAdapter:
    return _resolve(_ATS, name, "ats")  # type: ignore[no-any-return]


def all_portals() -> list[str]:
    return sorted(_PORTALS)


def all_ats() -> list[str]:
    return sorted(_ATS)


def broken_adapters() -> dict[str, str]:
    return dict(_BROKEN)
