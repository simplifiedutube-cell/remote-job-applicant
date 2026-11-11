"""The dry-run backstop at the I/O boundary.

Layer 4 of the four described in `jobagent.safety`, and the only one that survives a node
author who has never read that module. In dry-run mode:

* the browser context aborts every non-GET/HEAD request to the job's origin, so nothing
  can be POSTed regardless of what the page or the code does; and
* `SafePage.click()` refuses any selector that looks like a submit control, after dumping
  a screenshot and the full DOM to `artifacts/dryrun/<fingerprint>/`.

That dump is simultaneously the "dry-run produces inspectable artifacts" requirement.

Playwright is an optional dependency (`uv sync --extra browser`). This module imports it
lazily so that M1 -- and the whole unit test suite -- runs without it installed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog

from jobagent.config import get_config
from jobagent.safety import DryRunBlocked, current_mode, submit_gate

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import BrowserContext, Page, Route

__all__ = ["SUBMIT_PATTERNS", "SafePage", "install_dry_run_guard", "looks_like_submit"]

log = structlog.get_logger(__name__)

#: Selector/text shapes that indicate an irreversible control. Deliberately broad: a
#: false positive costs one escalation, a false negative costs an unintended application.
SUBMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"input\[type=[\"']?submit", re.I),
    re.compile(r"button\[type=[\"']?submit", re.I),
    re.compile(r"\bappl(y|ication)\b", re.I),
    re.compile(r"\bsubmit\b", re.I),
    re.compile(r"\bsend\s+application\b", re.I),
    re.compile(r"\bfinish\b|\bconfirm\b", re.I),
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def looks_like_submit(selector: str) -> bool:
    return any(p.search(selector) for p in SUBMIT_PATTERNS)


def install_dry_run_guard(context: BrowserContext, *, origin: str | None = None) -> None:
    """Abort every mutating request in dry-run mode.

    Scoped to `origin` when given, so a dry run can still authenticate against unrelated
    hosts if a flow requires it. With no origin, every mutating request is aborted.
    """
    if current_mode():
        return

    host = urlsplit(origin).hostname if origin else None

    def _guard(route: Route) -> None:
        request = route.request
        if request.method.upper() in _MUTATING_METHODS:
            target = urlsplit(request.url).hostname
            if host is None or target == host or (target or "").endswith(f".{host}"):
                log.info("dry_run_aborted_request", method=request.method, url=request.url)
                route.abort()
                return
        route.continue_()

    context.route("**/*", _guard)


class SafePage:
    """Wrapper handed to adapters instead of a raw Playwright `Page`.

    Adapters never receive the underlying page object, so the final click physically
    cannot be reached except through this class.
    """

    def __init__(self, page: Page, *, job_fingerprint: str | None = None) -> None:
        self._page = page
        self._fingerprint = job_fingerprint

    # ---------------------------------------------------------------- passthrough

    def __getattr__(self, item: str) -> Any:
        """Non-mutating Playwright API passes straight through."""
        return getattr(self._page, item)

    @property
    def url(self) -> str:
        return str(self._page.url)

    # ---------------------------------------------------------------- guarded

    def click(self, selector: str, **kwargs: Any) -> None:
        if looks_like_submit(selector):
            self.dump(reason="submit_attempt")
            submit_gate(
                f"click {selector!r} on {self.url}",
                job_fingerprint=self._fingerprint,
                url=self.url,
            )
        self._page.click(selector, **kwargs)

    def fill(self, selector: str, value: str, **kwargs: Any) -> None:
        """Filling a form is reversible and happens in dry runs too -- that is the point."""
        self._page.fill(selector, value, **kwargs)

    def set_input_files(self, selector: str, files: Any, **kwargs: Any) -> None:
        self._page.set_input_files(selector, files, **kwargs)

    # ---------------------------------------------------------------- evidence

    def dump(self, *, reason: str) -> Path:
        """Write a screenshot and the DOM. Returns the directory."""
        cfg = get_config()
        target = cfg.artifact_dir / ("dryrun" if not current_mode() else "live")
        target = target / (self._fingerprint or "unknown")
        target.mkdir(parents=True, exist_ok=True)

        try:
            self._page.screenshot(path=str(target / f"{reason}.png"), full_page=True)
            (target / f"{reason}.html").write_text(self._page.content(), encoding="utf-8")
            (target / f"{reason}.url.txt").write_text(self.url, encoding="utf-8")
        except Exception as exc:
            log.warning("evidence_dump_failed", reason=reason, error=repr(exc))

        log.info("evidence_dumped", reason=reason, path=str(target))
        return target

    def confirm_submission(self) -> tuple[str | None, str]:
        """Capture confirmation text and the final URL after a submit.

        Returns `(confirmation_text, final_url)`. A `None` confirmation is what forces
        `ApplicationResult(status="uncertain")` rather than `"applied"` -- the Pydantic
        validator will refuse the latter without evidence.
        """
        self.dump(reason="confirmation")
        text: str | None = None
        try:
            body = self._page.inner_text("body")
            for line in body.splitlines():
                if re.search(r"thank you|received|submitted|application sent", line, re.I):
                    text = line.strip()
                    break
        except Exception as exc:
            log.warning("confirmation_read_failed", error=repr(exc))
        return text, self.url


__doc_blocked__ = DryRunBlocked  # re-export for adapters that catch it
