"""Command line interface.

Note the asymmetry in `run`: there is no `--dry-run` flag because dry-run is the default,
and `--live` alone is not sufficient -- `JOBAGENT_ALLOW_LIVE=1` must also be set. A live
request that cannot be satisfied is refused loudly rather than silently downgraded,
because believing you applied when you did not is the worse failure.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jobagent.config import get_config
from jobagent.graph.state import Job, RunState, RunStats
from jobagent.logging import configure_logging
from jobagent.safety import LiveModeRefused, live_allowed, resolve_mode, run_mode

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Personal job-application automation. Dry-run by default.",
)
db_app = typer.Typer(no_args_is_help=True, help="Database lifecycle.")
app.add_typer(db_app, name="db")

console = Console()


def _bootstrap() -> None:
    cfg = get_config()
    configure_logging(level=cfg.log_level, json_output=cfg.log_json)


# --------------------------------------------------------------------------- doctor


@app.command()
def doctor() -> None:
    """Report every unmet dependency, before you hit it mid-run."""
    _bootstrap()
    cfg = get_config()
    table = Table(title="jobagent doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Blocks / note", style="dim")

    def row(name: str, ok: bool | None, note: str) -> None:
        mark = "[green]ok[/]" if ok else ("[yellow]n/a[/]" if ok is None else "[red]MISSING[/]")
        table.add_row(name, mark, note)

    # --- database
    db_ok = False
    db_note = "M1 integration tests, all runtime"
    try:
        from sqlalchemy import text

        from jobagent.store.session import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
        db_note = cfg.db_dsn
    except Exception as exc:
        db_note = f"{type(exc).__name__}: run `make up` (Docker Desktop must be running)"
    row("postgres + pgvector", db_ok, db_note)

    # --- alembic head
    try:
        from sqlalchemy import text

        from jobagent.store.session import get_engine

        with get_engine().connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM jobagent.alembic_version")).scalar()
        row("migrations", bool(rev), f"at revision {rev}" if rev else "run `make migrate`")
    except Exception:
        row("migrations", False, "run `make migrate`")

    # --- libreoffice
    soffice = shutil.which("soffice") or (
        str(cfg.soffice_bin) if cfg.soffice_bin.exists() else None
    )
    row(
        "libreoffice (soffice)",
        bool(soffice),
        soffice or "M4 pdf only -- .docx upload still works; brew install --cask libreoffice",
    )

    # --- playwright
    try:
        import playwright  # noqa: F401

        row("playwright", True, "M2 naukri, M5, M6")
    except ImportError:
        row(
            "playwright",
            False,
            "M2/M5/M6 -- uv sync --extra browser && playwright install chromium",
        )

    # --- embeddings
    try:
        import fastembed  # noqa: F401

        row("fastembed", True, f"{cfg.embed_model} @ {cfg.embed_dim}d")
    except ImportError:
        row("fastembed", False, "M2 dedupe, M4 evidence -- uv sync --extra embed")

    # --- llm
    if cfg.llm_provider == "claude_code":
        claude = shutil.which("claude")
        row("claude code sdk", bool(claude), claude or "M3/M4/M7 -- Claude Code CLI not on PATH")
    else:
        row("anthropic api key", bool(cfg.anthropic_api_key), "M3/M4/M7")

    # --- notify
    row("telegram", bool(cfg.telegram_bot_token and cfg.telegram_chat_id), "M7 escalation")
    row("gmail oauth", cfg.gmail_credentials_json.exists(), "M7 inbox -- slowest to obtain")

    # --- storage state
    states = (
        sorted(p.stem for p in cfg.storage_state_dir.glob("*.json"))
        if cfg.storage_state_dir.exists()
        else []
    )
    row(
        "browser sessions", bool(states), ", ".join(states) or "M6 -- run `jobagent login <portal>`"
    )

    # --- safety, last and loudest
    table.add_row(
        "[bold]submit mode[/]",
        "[red bold]LIVE[/]" if live_allowed(cfg) else "[green bold]dry-run[/]",
        f"dry_run={cfg.dry_run} allow_live={cfg.allow_live}",
    )

    console.print(table)

    # --- adapters
    from jobagent.adapters import ats as ats_pkg
    from jobagent.adapters import portals as portals_pkg
    from jobagent.adapters.registry import all_ats, all_portals, autodiscover, broken_adapters

    autodiscover(portals_pkg)
    autodiscover(ats_pkg)
    console.print(f"portals: {', '.join(all_portals()) or '(none)'}")
    console.print(f"ats:     {', '.join(all_ats()) or '(none)'}")
    if broken := broken_adapters():
        console.print(f"[red]broken adapters:[/] {broken}")


# --------------------------------------------------------------------------- graph


@app.command()
def graph(
    mermaid: Annotated[bool, typer.Option("--mermaid", help="Print Mermaid source.")] = True,
    xray: Annotated[bool, typer.Option("--xray/--no-xray", help="Expand subgraphs.")] = True,
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout.")] = None,
) -> None:
    """Render the whole graph. Requires no database."""
    from jobagent.graph.supervisor import render_mermaid

    text = render_mermaid(xray=xray)
    if not mermaid:  # pragma: no cover
        text = str(text)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"wrote {out}")
    else:
        console.print(text)


# --------------------------------------------------------------------------- db


@db_app.command("init")
def db_init() -> None:
    """Create the LangGraph checkpoint tables in the `langgraph` schema."""
    _bootstrap()
    from jobagent.store.checkpointer import setup_checkpointer

    setup_checkpointer()
    console.print("[green]checkpoint tables ready[/] (schema: langgraph)")


# --------------------------------------------------------------------------- export


@app.command()
def export() -> None:
    """Regenerate the CSV artifacts from Postgres."""
    _bootstrap()
    cfg = get_config()
    from jobagent.store.export import export_applications, export_daily_summary
    from jobagent.store.session import session_scope

    with session_scope() as session:
        rows = export_applications(session, cfg.export_dir / "applications.csv")
        export_daily_summary(session, cfg.export_dir / "daily-summary.csv")
    console.print(f"[green]exported[/] {rows} rows -> {cfg.export_dir / 'applications.csv'}")


# --------------------------------------------------------------------------- run


def _seed_jobs(path: Path | None) -> list[Job]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Job.model_validate(item) for item in payload]


@app.command()
def tracker(
    seed: Annotated[Path | None, typer.Option(help="JSON array of Job objects to persist.")] = None,
    run_id: Annotated[str | None, typer.Option(help="Reuse an existing run id.")] = None,
) -> None:
    """Run the tracker subgraph in isolation -- the M1 lane, end to end."""
    _bootstrap()
    from jobagent.graph.subgraphs.tracker import build_tracker_graph
    from jobagent.store.repo import RunRepo
    from jobagent.store.session import session_scope

    rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
    cfg = get_config()

    with session_scope() as session:
        if RunRepo(session).get(rid) is None:
            RunRepo(session).create(
                run_id=rid,
                thread_id=rid,
                profile_version="unversioned",
                dry_run=not live_allowed(cfg),
            )

    jobs = _seed_jobs(seed)
    state: RunState = {
        "run_id": rid,
        "profile_version": "unversioned",
        "search_specs": [],
        "raw_jobs": [],
        "jobs": jobs,
        "escalations": [],
        "errors": [],
        "stats": RunStats(dry_run=not live_allowed(cfg)),
    }

    with run_mode(False):
        result = build_tracker_graph().compile().invoke(state)

    console.print(f"[green]run {rid} complete[/]")
    console.print(result.get("stats"))


@app.command()
def run(
    live: Annotated[
        bool, typer.Option("--live", help="Submit for real. Also requires JOBAGENT_ALLOW_LIVE=1.")
    ] = False,
    run_id: Annotated[str | None, typer.Option(help="Explicit run id.")] = None,
) -> None:
    """Execute a full run. Dry-run unless BOTH --live and JOBAGENT_ALLOW_LIVE=1 are set.

    In M1 this stops at `discovery.plan_queries` with NotImplementedError, on purpose --
    the lane is scaffolded but not built. Use `jobagent tracker` for the implemented lane.
    """
    _bootstrap()
    cfg = get_config()
    try:
        effective_live = resolve_mode(requested_live=live, config=cfg)
    except LiveModeRefused as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc

    from jobagent.graph.supervisor import build_graph, initial_state
    from jobagent.store.checkpointer import checkpointer_scope
    from jobagent.store.repo import RunRepo
    from jobagent.store.session import session_scope

    rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
    with session_scope() as session:
        RunRepo(session).create(
            run_id=rid, thread_id=rid, profile_version="unversioned", dry_run=not effective_live
        )

    with checkpointer_scope() as saver, run_mode(effective_live):
        compiled = build_graph(checkpointer=saver)
        compiled.invoke(initial_state(rid), config={"configurable": {"thread_id": rid}})
    console.print(f"[green]run {rid} complete[/]")


@app.command()
def resume(run_id: Annotated[str, typer.Argument(help="Run id to resume.")]) -> None:
    """Resume an interrupted run. Re-crawls nothing and re-applies nothing."""
    _bootstrap()
    from jobagent.graph.supervisor import build_graph
    from jobagent.store.checkpointer import checkpointer_scope
    from jobagent.store.repo import RunRepo
    from jobagent.store.session import session_scope

    with session_scope() as session:
        row = RunRepo(session).get(run_id)
        if row is None:
            console.print(f"[red]unknown run_id {run_id}[/]")
            raise typer.Exit(code=1)
        was_live = not row.dry_run

    with checkpointer_scope() as saver, run_mode(was_live):
        compiled = build_graph(checkpointer=saver)
        # `None` as input tells LangGraph to continue from the persisted checkpoint
        # rather than start a fresh run.
        compiled.invoke(None, config={"configurable": {"thread_id": run_id}})
    console.print(f"[green]run {run_id} resumed to completion[/]")


@app.command()
def migrate() -> None:
    """Apply Alembic migrations (thin wrapper so the CLI is self-sufficient)."""
    _bootstrap()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)  # noqa: S607


if __name__ == "__main__":  # pragma: no cover
    app()
