"""Adapter registry: one file per board, no registration list."""

from __future__ import annotations

import re
import sys
import types
from typing import cast

import pytest

from jobagent.adapters import ats as ats_pkg
from jobagent.adapters import portals as portals_pkg
from jobagent.adapters.ratelimit import RateLimited, TokenBucket, domain_of
from jobagent.adapters.registry import (
    UnknownAdapter,
    all_ats,
    all_portals,
    autodiscover,
    broken_adapters,
    get_ats,
    get_portal,
    register_portal,
)
from jobagent.graph.state import Job, SearchSpec

EXPECTED_PORTALS = {
    "himalayas",
    "naukri",
    "remoteok",
    "remotive",
    "wellfound",
    "weworkremotely",
}
EXPECTED_ATS = {"ashby", "greenhouse", "lever", "workday"}


def test_every_adapter_file_registered_itself() -> None:
    """Autodiscovery, not an import list. Dropping in a file is the whole registration."""
    assert set(all_portals()) >= EXPECTED_PORTALS
    assert set(all_ats()) >= EXPECTED_ATS


def test_no_adapter_failed_to_import() -> None:
    assert broken_adapters() == {}


def test_unknown_name_error_lists_what_is_known() -> None:
    with pytest.raises(UnknownAdapter) as exc:
        get_portal("nope")
    message = str(exc.value)
    assert "remoteok" in message
    assert "broken=" in message


def test_missing_name_attribute_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"non-empty string \`name\`"):

        @register_portal
        class Nameless:
            pass


def test_duplicate_name_is_rejected() -> None:
    @register_portal
    class First:
        name = "dupe_probe"

    with pytest.raises(ValueError, match="duplicate portal name"):

        @register_portal
        class Second:
            name = "dupe_probe"


def test_broken_module_is_isolated_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A syntax error in one half-written adapter must not kill discovery for the rest."""
    pkg = types.ModuleType("fake_adapters")
    pkg.__path__ = []
    pkg.__name__ = "fake_adapters"

    def fake_iter(_path: object, prefix: str = "") -> list[object]:
        return [types.SimpleNamespace(name=f"{prefix}broken")]

    def fake_import(name: str) -> object:
        raise SyntaxError(f"unbalanced parenthesis in {name}")

    monkeypatch.setattr("jobagent.adapters.registry.pkgutil.iter_modules", fake_iter)
    monkeypatch.setattr("jobagent.adapters.registry.importlib.import_module", fake_import)

    autodiscover(pkg)  # must not raise

    assert any("broken" in key for key in broken_adapters())
    assert set(all_portals()) >= EXPECTED_PORTALS  # the healthy ones still work


# --------------------------------------------------------------- rate limiting


def test_get_portal_returns_a_rate_limited_proxy() -> None:
    """A node author cannot obtain an unthrottled adapter -- guardrail 6."""
    adapter = get_portal("remoteok")
    assert isinstance(adapter, RateLimited)
    assert adapter.name == "remoteok"


def test_stubs_raise_rather_than_silently_no_op() -> None:
    with pytest.raises(NotImplementedError, match=re.escape("M2: remoteok.search")):
        get_portal("remoteok").search(cast("SearchSpec", None))


def test_can_apply_returns_false_instead_of_raising() -> None:
    """The M5 router calls this on every adapter; a raising stub would break routing."""
    assert get_portal("naukri").can_apply(cast("Job", None)) is False


def test_token_bucket_allows_burst_then_throttles() -> None:
    bucket = TokenBucket(capacity=3.0, refill_per_second=1000.0)
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() > 0.0  # fourth must wait


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/1", "boards.greenhouse.io"),
        ("https://www.naukri.com/jobs", "naukri.com"),
    ],
)
def test_domain_of(url: str, expected: str) -> None:
    assert domain_of(url) == expected


# --------------------------------------------------------------- ats matching


@pytest.mark.parametrize(
    ("adapter", "url", "expected"),
    [
        ("greenhouse", "https://boards.greenhouse.io/acme/jobs/1", True),
        ("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/1", True),
        ("greenhouse", "https://jobs.lever.co/acme/1", False),
        ("lever", "https://jobs.lever.co/acme/1", True),
        ("ashby", "https://jobs.ashbyhq.com/acme/1", True),
        ("workday", "https://acme.wd1.myworkdayjobs.com/careers", True),
        ("workday", "https://example.com/careers", False),
    ],
)
def test_ats_host_matching(adapter: str, url: str, expected: bool) -> None:
    assert get_ats(adapter).matches(url) is expected


def test_packages_are_importable_without_optional_deps() -> None:
    """M1 must run with neither playwright nor fastembed installed."""
    assert portals_pkg.__name__ in sys.modules
    assert ats_pkg.__name__ in sys.modules
