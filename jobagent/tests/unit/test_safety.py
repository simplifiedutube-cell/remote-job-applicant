"""Dry-run defaults and the two-key rule.

The threat model is "a node author forgets to check the flag", so the tests here assert
that forgetting is survivable: the default is safe, a flag alone is not enough, and the
selector guard catches submit-shaped clicks without any cooperation from the caller.
"""

from __future__ import annotations

import pytest

from jobagent.adapters.browser.safe_page import SUBMIT_PATTERNS, looks_like_submit
from jobagent.config import RunConfig
from jobagent.safety import (
    DryRunBlocked,
    LiveModeRefused,
    current_mode,
    live_allowed,
    resolve_mode,
    run_mode,
    submit_gate,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("JOBAGENT_DRY_RUN", "JOBAGENT_ALLOW_LIVE"):
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------- defaults


def test_default_is_dry_run(clean_env: None) -> None:
    """An unconfigured process must be incapable of submitting."""
    assert RunConfig(_env_file=None).dry_run is True
    assert RunConfig(_env_file=None).allow_live is False


def test_default_context_is_not_live() -> None:
    assert current_mode() is False


# --------------------------------------------------------------- two-key rule


@pytest.mark.parametrize(
    ("dry_run", "allow_live", "expected"),
    [
        (True, True, False),  # allow_live alone is not enough
        (False, False, False),  # clearing dry_run alone is not enough
        (True, False, False),
        (False, True, True),  # both keys
    ],
)
def test_live_requires_both_keys(dry_run: bool, allow_live: bool, expected: bool) -> None:
    cfg = RunConfig(_env_file=None, dry_run=dry_run, allow_live=allow_live)
    assert live_allowed(cfg) is expected


def test_live_flag_without_env_key_is_refused_loudly() -> None:
    """Silently downgrading --live to a dry run would be worse than an error.

    You would believe applications were sent when nothing was.
    """
    cfg = RunConfig(_env_file=None, dry_run=False, allow_live=False)
    with pytest.raises(LiveModeRefused, match="JOBAGENT_ALLOW_LIVE"):
        resolve_mode(requested_live=True, config=cfg)


def test_no_live_request_is_always_a_dry_run() -> None:
    cfg = RunConfig(_env_file=None, dry_run=False, allow_live=True)
    assert resolve_mode(requested_live=False, config=cfg) is False


def test_live_request_with_both_keys_resolves_live() -> None:
    cfg = RunConfig(_env_file=None, dry_run=False, allow_live=True)
    assert resolve_mode(requested_live=True, config=cfg) is True


# --------------------------------------------------------------- the gate


def test_submit_gate_blocks_in_dry_run() -> None:
    with run_mode(False), pytest.raises(DryRunBlocked, match="refused to"):
        submit_gate("click submit")


def test_submit_gate_permits_in_live_mode() -> None:
    with run_mode(True):
        submit_gate("click submit")  # does not raise


def test_run_mode_restores_previous_mode() -> None:
    with run_mode(True):
        assert current_mode() is True
    assert current_mode() is False


def test_run_mode_restores_after_exception() -> None:
    with pytest.raises(RuntimeError), run_mode(True):
        raise RuntimeError("boom")
    assert current_mode() is False


# --------------------------------------------------------------- selector guard


@pytest.mark.parametrize(
    "selector",
    [
        "input[type=submit]",
        "button[type='submit']",
        "text=Submit application",
        "#apply-now",
        "button:has-text('Apply')",
        ".application-submit",
        "text=Confirm",
    ],
)
def test_submit_shaped_selectors_are_detected(selector: str) -> None:
    assert looks_like_submit(selector)


@pytest.mark.parametrize(
    "selector",
    ["input[name=email]", "#first_name", "textarea.cover-letter", "a.job-title"],
)
def test_ordinary_selectors_pass(selector: str) -> None:
    assert not looks_like_submit(selector)


def test_patterns_are_non_empty() -> None:
    assert len(SUBMIT_PATTERNS) >= 5
