"""FastAPI control plane + APScheduler -- M7.

Endpoints planned: run trigger, run status, open escalations, resolve an escalation
(which resumes the interrupted graph on its thread).

Scheduled jobs: a daily discovery/apply run, the inbox watcher on a short interval, and
the 8pm digest.
"""

from __future__ import annotations

from fastapi import FastAPI

__all__ = ["app", "create_app"]


def create_app() -> FastAPI:
    api = FastAPI(title="jobagent", version="0.1.0")

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()


def start_scheduler() -> None:
    """M7: APScheduler -- daily run, inbox poll, 8pm digest."""
    raise NotImplementedError("M7: api.start_scheduler")
